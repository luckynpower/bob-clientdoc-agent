"""Tests for the merged escalation-history timeline (Phase 3 dashboard).

Hermetic — no Turso, no network. Exercises the real
Storage.get_client_timeline() merge/sort logic by feeding it fake
reminder_log / review_queue readers (so no DB connection is opened).

Run:  python -m tests.test_timeline
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.storage import Storage

results = []


def check(name, condition):
    results.append((name, bool(condition)))
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}")


class FakeTimelineStorage(Storage):
    """Storage subclass that skips the DB and serves canned rows.

    Overrides only the two leaf readers get_client_timeline() depends on, so
    the real merge/sort/label logic runs unchanged.
    """

    def __init__(self, reminders, reviews):
        # Deliberately do NOT call super().__init__ (no Turso connection).
        self._reminders = reminders
        self._reviews = reviews

    def get_reminder_history(self, client_id):
        return [r for r in self._reminders if r["client_id"] == client_id]

    def get_review_items_for_client(self, client_id):
        # Backfill the decision/note fields the real reader returns so canned
        # rows in these tests don't have to specify them all.
        out = []
        for r in self._reviews:
            if r["client_id"] != client_id:
                continue
            row = {
                "detail": None, "decision": None, "note": None,
                "decided_at": None, "decided_by": None,
            }
            row.update(r)
            out.append(row)
        return out


def main() -> int:
    # ---- Merge order (newest first) + legacy label -------------------
    print("merge order (newest first) + source/legacy:")
    reminders = [
        {"id": 1, "client_id": "CLX", "tier": 0, "message_text": "t0 msg",
         "timestamp": "2026-01-01T09:00:00+00:00", "source": "template_fallback"},
        {"id": 2, "client_id": "CLX", "tier": 1, "message_text": "t1 msg",
         "timestamp": "2026-01-05T09:00:00+00:00", "source": "llm"},
        # pre-migration row: no source -> should surface as "legacy"
        {"id": 3, "client_id": "CLX", "tier": 2, "message_text": "t2 msg",
         "timestamp": "2026-01-09T09:00:00+00:00", "source": None},
    ]
    reviews = [
        {"id": 10, "client_id": "CLX", "reason": "tier_3_escalation",
         "created_at": "2026-01-14T09:00:00+00:00", "resolved": False},
    ]
    tl = FakeTimelineStorage(reminders, reviews).get_client_timeline("CLX")

    times = [e["timestamp"] for e in tl]
    check("timeline sorted newest first", times == sorted(times, reverse=True))
    check("first entry is the newest (tier 3 escalation on 01-14)",
          tl[0]["kind"] == "tier_3_escalation")
    check("all four events present", len(tl) == 4)

    # legacy label
    t2 = next(e for e in tl if e["kind"] == "reminder" and e["tier"] == 2)
    check("blank source rendered as 'legacy'", t2["source"] == "legacy")
    t1 = next(e for e in tl if e["kind"] == "reminder" and e["tier"] == 1)
    check("llm source preserved", t1["source"] == "llm")
    t0 = next(e for e in tl if e["kind"] == "reminder" and e["tier"] == 0)
    check("template_fallback source preserved", t0["source"] == "template_fallback")

    # ---- Tier 3 client timeline shows the escalation -----------------
    print("\nTier 3 client: escalation appears in the timeline:")
    reminders_t3 = [
        {"id": 1, "client_id": "CL9", "tier": 0, "message_text": "hi",
         "timestamp": "2026-02-01T09:00:00+00:00", "source": "llm"},
        {"id": 2, "client_id": "CL9", "tier": 1, "message_text": "nudge",
         "timestamp": "2026-02-04T09:00:00+00:00", "source": "llm"},
        {"id": 3, "client_id": "CL9", "tier": 2, "message_text": "firm",
         "timestamp": "2026-02-08T09:00:00+00:00", "source": "llm"},
    ]
    reviews_t3 = [
        {"id": 20, "client_id": "CL9", "reason": "tier_3_escalation",
         "created_at": "2026-02-15T09:00:00+00:00", "resolved": False},
    ]
    tl3 = FakeTimelineStorage(reminders_t3, reviews_t3).get_client_timeline("CL9")
    esc = [e for e in tl3 if e["kind"] == "tier_3_escalation"]
    check("exactly one tier_3_escalation event", len(esc) == 1)
    check("escalation is newest (first) in timeline", tl3[0]["kind"] == "tier_3_escalation")
    check("escalation carries reason text as text", esc[0]["text"] == "tier_3_escalation")
    check("escalation tier is None (not a reminder)", esc[0]["tier"] is None)
    check("escalation source is None", esc[0]["source"] is None)
    check("escalation shows resolved=False (open)", esc[0]["resolved"] is False)
    check("3 reminders still present below the escalation",
          sum(1 for e in tl3 if e["kind"] == "reminder") == 3)

    # ---- Resolved review item included with resolved status ----------
    print("\nresolved review items are included with status:")
    reviews_resolved = [
        {"id": 30, "client_id": "CLR", "reason": "ambiguous_document",
         "created_at": "2026-03-01T09:00:00+00:00", "resolved": True},
    ]
    tlr = FakeTimelineStorage([], reviews_resolved).get_client_timeline("CLR")
    check("resolved review item present", len(tlr) == 1)
    check("ambiguous_document kind preserved", tlr[0]["kind"] == "ambiguous_document")
    check("resolved flag True surfaced", tlr[0]["resolved"] is True)

    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"\n{passed}/{total} checks passed.")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
