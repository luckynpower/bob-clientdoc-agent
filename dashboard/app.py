"""Phase 3 — Account Manager Dashboard (Flask).

A read-mostly view over the same Turso database Phase 2 populates. It is a
separate, independent consumer of that data: it does NOT run the agent
loop and makes no assumption about what produced the rows.

Auth: the WHOLE dashboard (every GET and POST route) is gated with HTTP
Basic auth — username DASHBOARD_USER, password DASHBOARD_AUTH_TOKEN (from
.env). The single write action (approve/dismiss) is additionally CSRF-
protected with a signed-session token.

Routes:
  GET  /                               client overview
  GET  /client/<client_id>/history     that client's merged audit timeline
  GET  /review-queue                   open (undecided) review items
  POST /review-queue/<id>/decide       approve/dismiss ONE open item

Data access is via src.storage.Storage only. The only write performed here
is decide_review_item (approve/dismiss); no deletes, no other writes.
"""
from __future__ import annotations

import hmac
import secrets
import sys
from functools import wraps
from pathlib import Path

# Make the Phase 2 `src` package importable when run from anywhere.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from flask import (  # noqa: E402
    Flask,
    Response,
    abort,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from src import config  # noqa: E402
from src.storage import Storage  # noqa: E402
from src.tiers import get_tier  # noqa: E402

app = Flask(__name__)
# SECRET_KEY signs the session cookie used for CSRF. Fall back to a random
# per-process key if unset (dev only) so the app still boots.
app.secret_key = config.DASHBOARD_SECRET_KEY or secrets.token_hex(32)

# Human-readable labels for the machine reason codes Phase 2 writes.
REASON_LABELS = {
    "tier_3_escalation": "Tier 3 — unresponsive (3+ reminders / 14+ days)",
    "ambiguous_document": "Ambiguous document — could not verify",
}

# Type-badge labels for the merged escalation-history timeline.
TIMELINE_KIND_LABELS = {
    "reminder": "Reminder",
    "tier_3_escalation": "Tier 3 escalation",
    "ambiguous_document": "Ambiguous document",
}


def _open_storage() -> Storage:
    """Fresh connection per request (libSQL sync client isn't shared-safe)."""
    return Storage()


# ---------------------------------------------------------------------------
# Auth (Basic) — gates every route
# ---------------------------------------------------------------------------
def _auth_ok(auth) -> bool:
    if auth is None:
        return False
    expected_user = config.DASHBOARD_USER
    expected_token = config.DASHBOARD_AUTH_TOKEN
    # Constant-time comparison for both fields.
    user_ok = hmac.compare_digest(auth.username or "", expected_user)
    token_ok = hmac.compare_digest(auth.password or "", expected_token)
    return user_ok and token_ok


@app.before_request
def _require_auth():
    # Refuse to run at all if auth isn't configured — never serve unprotected.
    if not config.dashboard_auth_configured():
        return Response(
            "Dashboard auth is not configured. Set DASHBOARD_AUTH_TOKEN in .env.",
            500,
        )
    if not _auth_ok(request.authorization):
        return Response(
            "Authentication required.",
            401,
            {"WWW-Authenticate": 'Basic realm="Maple & Co Dashboard"'},
        )
    return None


def current_user() -> str:
    auth = request.authorization
    return auth.username if auth and auth.username else config.DASHBOARD_USER


# ---------------------------------------------------------------------------
# CSRF (signed-session token, no extra dependency)
# ---------------------------------------------------------------------------
def _csrf_token() -> str:
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


@app.context_processor
def _inject_csrf():
    # Makes csrf_token() available to templates for the hidden form field.
    return {"csrf_token": _csrf_token}


def _csrf_valid() -> bool:
    sent = request.form.get("csrf_token", "")
    expected = session.get("csrf_token", "")
    return bool(expected) and hmac.compare_digest(sent, expected)


def csrf_protect(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not _csrf_valid():
            abort(400, description="Invalid or missing CSRF token.")
        return view(*args, **kwargs)

    return wrapped


# ---------------------------------------------------------------------------
# Read routes
# ---------------------------------------------------------------------------
@app.route("/")
def overview():
    with _open_storage() as db:
        rows = db.list_clients_overview()

    clients = []
    for r in rows:
        tier = get_tier(r["days_since_first_request"], r["reminders_sent"])
        outstanding = r["missing_count"] + r["ambiguous_count"]
        clients.append(
            {
                **r,
                "tier": tier,
                "outstanding": outstanding,
                "needs_attention": outstanding > 0 and tier >= 2,
            }
        )

    clients.sort(
        key=lambda c: (
            not c["needs_attention"],
            -c["outstanding"],
            -c["tier"],
            c["client_id"],
        )
    )
    return render_template("overview.html", clients=clients)


@app.route("/client/<client_id>/history")
def history(client_id: str):
    with _open_storage() as db:
        client = db.get_client(client_id)
        if client is None:
            abort(404, description=f"Unknown client {client_id}")
        timeline = db.get_client_timeline(client_id)
    # Attach a display label per entry; message text itself is left untouched
    # and rendered via Jinja autoescaping (LLM output is untrusted).
    for e in timeline:
        e["kind_label"] = TIMELINE_KIND_LABELS.get(e["kind"], e["kind"])
    return render_template("history.html", client=client, timeline=timeline)


@app.route("/review-queue")
def review_queue():
    with _open_storage() as db:
        items = db.get_open_review_queue()
    for it in items:
        it["reason_label"] = REASON_LABELS.get(it["reason"], it["reason"])
    return render_template("review_queue.html", items=items)


# ---------------------------------------------------------------------------
# Write route — the only write the dashboard performs
# ---------------------------------------------------------------------------
@app.route("/review-queue/<int:review_id>/decide", methods=["POST"])
@csrf_protect
def decide(review_id: int):
    decision = (request.form.get("decision") or "").strip()
    note = (request.form.get("note") or "").strip() or None

    if decision not in ("approved", "dismissed"):
        abort(400, description="decision must be 'approved' or 'dismissed'.")

    with _open_storage() as db:
        outcome = db.decide_review_item(
            review_id, decision, note, current_user()
        )

    if outcome == Storage.DECIDE_OK:
        return redirect(url_for("review_queue"))
    if outcome == Storage.DECIDE_NOT_FOUND:
        abort(404, description="Review item not found.")
    if outcome == Storage.DECIDE_ALREADY:
        abort(409, description="This review item has already been decided.")
    # DECIDE_INVALID (belt-and-braces; we validated above)
    abort(400, description="Invalid decision.")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
