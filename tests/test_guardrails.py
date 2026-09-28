"""Focused checks for the Phase 2 guardrails and tier logic.

These do NOT touch Turso — they use an in-memory fake Storage and monkeypatch
check_completeness so the two hard guardrails can be exercised directly
(the shipped dataset has no scanned/ambiguous PDFs, so the ambiguous path
would otherwise never fire).

Run with:  python -m tests.test_guardrails   (or: python tests/test_guardrails.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import agent
from src.agent import (
    ACTION_COMPLETE,
    ACTION_ESCALATE_TIER3,
    ACTION_REMINDER,
    ACTION_REVIEW_AMBIGUOUS,
    process_client,
)
from src.clients import Client
from src.reminders import draft_reminder
from src.tiers import get_tier


class FakeStorage:
    """Minimal in-memory stand-in for the Turso Storage."""

    def __init__(self):
        self.documents = []
        self.reminders = []
        self.review_queue = []

    def upsert_document(self, client_id, label, status, file_path=None):
        self.documents.append((client_id, label, status, file_path))

    def has_open_review_item(self, client_id):
        return any(rq[0] == client_id for rq in self.review_queue)

    def reminder_exists(self, client_id, tier):
        return any(r[0] == client_id and r[1] == tier for r in self.reminders)

    def log_reminder_once(self, client_id, tier, message_text, source=None):
        # Mirror the real idempotency: one row per (client_id, tier).
        if any(r[0] == client_id and r[1] == tier for r in self.reminders):
            return False
        self.reminders.append((client_id, tier, message_text, source))
        return True

    def add_to_review_queue(self, client_id, reason, detail=None):
        # Idempotent for open items, like the real Storage.
        if (client_id, reason) in [(c, r) for c, r in self.review_queue]:
            return
        self.review_queue.append((client_id, reason))


def _client(**kw) -> Client:
    base = dict(
        client_id="CLTEST",
        client_name="Test Co",
        client_type="Sole Proprietor",
        gst_registered=False,
        required_documents=["Bank statement"],
        submitted_documents=[],
        missing_documents=["Bank statement"],
        submission_status="Partial",
        days_since_first_request=0,
        reminders_sent=0,
        last_contact_channel="Email",
        contact_email="test@example.com",
    )
    base.update(kw)
    return Client(**base)


def _patch_completeness(status_map):
    """Force stage 2 to return {label: (status, file_path)} with no writes."""
    shaped = {label: (status, None) for label, status in status_map.items()}
    agent.check_completeness = lambda client: shaped


results = []


def check(name, condition):
    results.append((name, bool(condition)))
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}")


def main() -> int:
    print("get_tier thresholds (from week1_notes.md):")
    check("day 0 -> tier 0", get_tier(0, 0) == 0)
    check("day 3 -> tier 1", get_tier(3, 0) == 1)
    check("day 5 -> tier 1", get_tier(5, 0) == 1)
    check("day 7 -> tier 2", get_tier(7, 0) == 2)
    check("day 10 -> tier 2", get_tier(10, 0) == 2)
    check("day 14 -> tier 3", get_tier(14, 0) == 3)
    check("3 reminders -> tier 3 regardless of days", get_tier(1, 3) == 3)
    check("5 reminders -> tier 3", get_tier(7, 5) == 3)

    print("\ndraft_reminder guardrail:")
    check("tier 3 text is None", draft_reminder(_client(), 3).text is None)
    check("tier 0 text is a string", isinstance(draft_reminder(_client(), 0).text, str))
    check("tier 2 text is a string", isinstance(draft_reminder(_client(), 2).text, str))

    print("\nprocess_client: Tier 3 escalation (no client message):")
    _patch_completeness({"Bank statement": "missing"})
    st = FakeStorage()
    c = _client(days_since_first_request=20, reminders_sent=3)
    r = process_client(c, st)
    check("action is escalated_tier3", r.action == ACTION_ESCALATE_TIER3)
    check("no reminder logged", st.reminders == [])
    check("reminder_text is None", r.reminder_text is None)
    check("routed to review_queue as tier_3_escalation",
          ("CLTEST", "tier_3_escalation") in st.review_queue)

    print("\nprocess_client: ambiguous doc -> review regardless of tier:")
    _patch_completeness({"Bank statement": "ambiguous"})
    st = FakeStorage()
    c = _client(days_since_first_request=1, reminders_sent=0)  # would be tier 0
    r = process_client(c, st)
    check("action is review_ambiguous_doc", r.action == ACTION_REVIEW_AMBIGUOUS)
    check("no reminder logged", st.reminders == [])
    check("routed to review_queue as ambiguous_document",
          ("CLTEST", "ambiguous_document") in st.review_queue)

    print("\nprocess_client: Tier 2 with missing docs -> reminder drafted:")
    _patch_completeness({"Bank statement": "missing"})
    st = FakeStorage()
    c = _client(days_since_first_request=8, reminders_sent=1)
    r = process_client(c, st)
    check("action is reminder_drafted", r.action == ACTION_REMINDER)
    check("exactly one reminder logged", len(st.reminders) == 1)
    check("reminder tier is 2", st.reminders and st.reminders[0][1] == 2)

    print("\nprocess_client: complete client -> no action even at Tier 3:")
    _patch_completeness({"Bank statement": "submitted"})
    st = FakeStorage()
    c = _client(days_since_first_request=30, reminders_sent=0, missing_documents=[])
    r = process_client(c, st)
    check("action is no_action_complete", r.action == ACTION_COMPLETE)
    check("no reminder logged", st.reminders == [])
    check("not escalated", st.review_queue == [])

    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"\n{passed}/{total} checks passed.")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
