"""Dashboard tests: Basic auth, CSRF, and the approve/dismiss decide route.

Hermetic — no Turso, no network. Uses Flask's test client and a fake storage
injected via dashboard.app._open_storage. Auth config is set on src.config
before the app enforces it.

Run:  python -m tests.test_dashboard
"""
from __future__ import annotations

import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config

# Configure auth BEFORE importing the app's before_request logic runs.
config.DASHBOARD_USER = "manager"
config.DASHBOARD_AUTH_TOKEN = "secret-token"
config.DASHBOARD_SECRET_KEY = "test-secret-key-for-csrf"

from dashboard import app as dash_app  # noqa: E402

results = []


def check(name, condition):
    results.append((name, bool(condition)))
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}")


class FakeReviewStorage:
    """Minimal storage stand-in for the decide/review routes."""

    DECIDE_OK = "ok"
    DECIDE_INVALID = "invalid_decision"
    DECIDE_NOT_FOUND = "not_found"
    DECIDE_ALREADY = "already_decided"

    def __init__(self):
        # id -> row
        self.rows = {
            1: {"id": 1, "client_id": "CL002", "client_name": "Wagner Inc",
                "reason": "tier_3_escalation", "created_at": "2026-01-01T00:00:00+00:00",
                "resolved": 0, "decision": None, "note": None,
                "decided_at": None, "decided_by": None},
        }
        self.writes = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_open_review_queue(self):
        out = []
        for r in self.rows.values():
            if r["resolved"] == 0:
                out.append({
                    "id": r["id"], "client_id": r["client_id"],
                    "client_name": r["client_name"], "reason": r["reason"],
                    "created_at": r["created_at"],
                })
        return out

    def decide_review_item(self, review_id, decision, note, decided_by):
        if decision not in ("approved", "dismissed"):
            return self.DECIDE_INVALID
        r = self.rows.get(review_id)
        if r is None:
            return self.DECIDE_NOT_FOUND
        if r["resolved"] == 1 or r["decision"] is not None:
            return self.DECIDE_ALREADY
        r.update(resolved=1, decision=decision, note=note,
                 decided_at="2026-02-02T00:00:00+00:00", decided_by=decided_by)
        self.writes.append((review_id, decision, note, decided_by))
        return self.DECIDE_OK


def _auth_header(user="manager", token="secret-token"):
    raw = f"{user}:{token}".encode()
    return {"Authorization": "Basic " + base64.b64encode(raw).decode()}


def _get_csrf(client):
    """Load the review queue (authed) and pull the CSRF token from the form."""
    resp = client.get("/review-queue", headers=_auth_header())
    html = resp.get_data(as_text=True)
    import re
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    return m.group(1) if m else None


def main() -> int:
    dash_app.app.config["TESTING"] = True

    # ---- Auth ---------------------------------------------------------
    print("Basic auth gates the whole dashboard:")
    fake = FakeReviewStorage()
    dash_app._open_storage = lambda: fake
    client = dash_app.app.test_client()

    r_noauth = client.get("/review-queue")
    check("no auth -> 401 on GET", r_noauth.status_code == 401)
    check("401 sets WWW-Authenticate", "WWW-Authenticate" in r_noauth.headers)

    r_badauth = client.get("/review-queue", headers=_auth_header(token="wrong"))
    check("bad token -> 401", r_badauth.status_code == 401)

    r_ok = client.get("/review-queue", headers=_auth_header())
    check("valid auth -> 200", r_ok.status_code == 200)

    # ---- CSRF ---------------------------------------------------------
    print("\nCSRF protection on the decide POST:")
    # No CSRF token -> 400
    r_nocsrf = client.post(
        "/review-queue/1/decide",
        data={"decision": "approved"},
        headers=_auth_header(),
    )
    check("missing CSRF token -> 400", r_nocsrf.status_code == 400)

    # Bad CSRF token -> 400
    r_badcsrf = client.post(
        "/review-queue/1/decide",
        data={"decision": "approved", "csrf_token": "not-the-token"},
        headers=_auth_header(),
    )
    check("bad CSRF token -> 400", r_badcsrf.status_code == 400)

    # ---- Single decision succeeds ------------------------------------
    print("\nsingle decision succeeds and records fields:")
    # Capture the CSRF token while the item is still open (the token is
    # per-session, so it stays valid for the follow-up POST too).
    token = _get_csrf(client)
    check("csrf token present in form", bool(token))
    r_decide = client.post(
        "/review-queue/1/decide",
        data={"decision": "dismissed", "note": "false alarm", "csrf_token": token},
        headers=_auth_header(),
    )
    check("valid decide -> 302 redirect", r_decide.status_code == 302)
    row = fake.rows[1]
    check("decision recorded", row["decision"] == "dismissed")
    check("note recorded", row["note"] == "false alarm")
    check("decided_by is the auth user", row["decided_by"] == "manager")
    check("resolved set to 1", row["resolved"] == 1)

    # ---- Second decision rejected ------------------------------------
    print("\nsecond decision on same item is rejected:")
    # Reuse the same session token (item is now closed, so the form no longer
    # renders it — but the session token is still valid).
    r_second = client.post(
        "/review-queue/1/decide",
        data={"decision": "approved", "note": "changed mind", "csrf_token": token},
        headers=_auth_header(),
    )
    check("second decision -> 409", r_second.status_code == 409)
    check("original decision unchanged", fake.rows[1]["decision"] == "dismissed")

    # ---- Invalid decision value --------------------------------------
    print("\ninvalid decision value rejected:")
    fake2 = FakeReviewStorage()
    dash_app._open_storage = lambda: fake2
    token3 = _get_csrf(client)
    r_bad = client.post(
        "/review-queue/1/decide",
        data={"decision": "maybe", "csrf_token": token3},
        headers=_auth_header(),
    )
    check("invalid decision -> 400", r_bad.status_code == 400)
    check("no write happened", fake2.writes == [])

    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"\n{passed}/{total} checks passed.")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
