"""Manual check for the review-queue resolve flow (Phase 3).

Prints the open review queue, resolves one item via the same storage
method the dashboard uses, then re-reads to confirm the count drops by
exactly one and stays consistent. Restores the item afterwards so the
demo data is left as it was found.

Run:  python -m dashboard.check_resolve   (from the project root)
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.storage import Storage


def _print_queue(db, label):
    items = db.get_open_review_queue()
    print(f"\n{label} — {len(items)} open item(s):")
    for it in items:
        print(f"  #{it['id']:<3} {it['client_id']:<7} {it['reason']}")
    return items


def main() -> int:
    with Storage() as db:
        before = _print_queue(db, "BEFORE")
        if not before:
            print("\nReview queue is empty — run `python run_cycle.py` first.")
            return 1

        target = before[0]
        print(f"\nResolving item #{target['id']} ({target['client_id']}) ...")
        changed = db.resolve_review_item(target["id"])
        print(f"  resolve_review_item returned: {changed}")

        after = _print_queue(db, "AFTER")

        ok = len(after) == len(before) - 1
        ok = ok and all(it["id"] != target["id"] for it in after)

        # Re-read to confirm consistency on a second load.
        reload_count = db.count_open_review_queue()
        ok = ok and reload_count == len(after)
        print(f"\nReload count: {reload_count} (expected {len(after)})")

        # Restore so repeated runs behave identically.
        db.execute(
            "UPDATE review_queue SET resolved = 0 WHERE id = ?", [target["id"]]
        )
        print(f"Restored item #{target['id']} (test cleanup).")

        print("\nRESULT:", "PASS" if ok else "FAIL")
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
