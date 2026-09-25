"""Phase 3 — Account Manager Dashboard (Flask).

A read-mostly view over the same Turso database Phase 2 populates. It is a
separate, independent consumer of that data: it does NOT run the agent
loop and makes no assumption about what produced the rows (standalone
script or, later, an OpenClaw-hosted agent).

Routes:
  GET  /                              client overview
  GET  /client/<client_id>/history    that client's reminder log
  GET  /review-queue                  open (unresolved) review items
  POST /review-queue/<id>/resolve     mark one item resolved, then redirect

Data access is via src.storage.Storage only (no second Turso module).
The only write performed anywhere here is flipping review_queue.resolved.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make the Phase 2 `src` package importable when run from anywhere.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from flask import Flask, abort, redirect, render_template, url_for  # noqa: E402

from src.storage import Storage  # noqa: E402
from src.tiers import get_tier  # noqa: E402

app = Flask(__name__)

# Human-readable labels for the machine reason codes Phase 2 writes.
REASON_LABELS = {
    "tier_3_escalation": "Tier 3 — unresponsive (3+ reminders / 14+ days)",
    "ambiguous_document": "Ambiguous document — could not verify",
}


def _open_storage() -> Storage:
    """Fresh connection per request (libSQL sync client isn't shared-safe)."""
    return Storage()


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
                # Mirror Phase 2's gate: a client only needs attention if it
                # actually has something outstanding. A high tier on a client
                # with nothing missing is not "overdue".
                "needs_attention": outstanding > 0 and tier >= 2,
            }
        )

    # Sort: attention cases first, then by outstanding count, then tier, then id.
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
        entries = db.get_reminder_history(client_id)
    return render_template("history.html", client=client, entries=entries)


@app.route("/review-queue")
def review_queue():
    with _open_storage() as db:
        items = db.get_open_review_queue()
    for it in items:
        it["reason_label"] = REASON_LABELS.get(it["reason"], it["reason"])
    return render_template("review_queue.html", items=items)


@app.route("/review-queue/<int:review_id>/resolve", methods=["POST"])
def resolve(review_id: int):
    with _open_storage() as db:
        db.resolve_review_item(review_id)
    return redirect(url_for("review_queue"))


if __name__ == "__main__":
    # Debug reloader off so it runs cleanly as a single process.
    app.run(host="127.0.0.1", port=5000, debug=False)
