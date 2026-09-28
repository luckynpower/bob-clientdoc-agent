"""Phase 2 runner.

Default mode: runs run_cycle() against all 20 clients, prints a human
summary table plus a cross-check against the CSV's own submission_status
and missing_documents columns.

--json mode: prints ONE JSON object per client to stdout and nothing else;
all logs are routed to stderr. Each object has:
    client_id, missing_docs, tier, action (none|remind|escalate_to_human),
    reason

Usage:
    python run_cycle.py           # human table (default)
    python run_cycle.py --json    # one JSON object per client on stdout

Requires Turso credentials in .env (TURSO_DATABASE_URL, TURSO_AUTH_TOKEN).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from src import config
from src.agent import run_cycle
from src.clients import read_clients_csv
from src.storage import Storage
from src.tiers import get_tier


def _fmt_list(items):
    return ", ".join(items) if items else "-"


def _configure_logging(json_mode: bool) -> None:
    """Logs always go to stderr. In JSON mode stdout is reserved for JSON."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s" if not json_mode else "%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def _run_json() -> int:
    """Emit one JSON object per client on stdout; logs already go to stderr."""
    with Storage() as storage:
        results = run_cycle(storage)
        for r in results:
            # One compact JSON object per line (stdout only).
            sys.stdout.write(json.dumps(r.to_json_dict()) + "\n")
    return 0


def _run_human() -> int:
    # Ground-truth lookup from the CSV for verification.
    csv_clients = {c.client_id: c for c in read_clients_csv()}

    with Storage() as storage:
        results = run_cycle(storage)

        print("\n" + "=" * 92)
        print("PHASE 2 - TRACKING + REMINDER LOOP: CYCLE SUMMARY")
        print("=" * 92)
        header = f"{'CLIENT':<7} {'TIER':<5} {'ACTION':<22} {'MISSING (detected)':<45}"
        print(header)
        print("-" * 92)
        for r in results:
            print(
                f"{r.client_id:<7} {r.tier:<5} {r.action:<22} "
                f"{_fmt_list(r.missing_documents):<45}"
            )

        # -- Verification against the CSV ---------------------------------
        print("\n" + "=" * 92)
        print("VERIFICATION AGAINST CSV (submission_status + missing_documents)")
        print("=" * 92)
        print(
            f"{'CLIENT':<7} {'TIER':<5} {'MISSING MATCH':<14} "
            f"{'CSV STATUS':<24} {'NOTES':<30}"
        )
        print("-" * 92)

        mismatches = 0
        for r in results:
            c = csv_clients[r.client_id]
            expected_tier = get_tier(c.days_since_first_request, c.reminders_sent)
            tier_ok = expected_tier == r.tier

            detected = set(m.strip() for m in r.missing_documents)
            expected_missing = set(m.strip() for m in c.missing_documents)
            missing_ok = detected == expected_missing

            notes = []
            if not tier_ok:
                notes.append(f"tier!={expected_tier}")
            if not missing_ok:
                only_detected = detected - expected_missing
                only_expected = expected_missing - detected
                if only_detected:
                    notes.append(f"+{_fmt_list(sorted(only_detected))}")
                if only_expected:
                    notes.append(f"-{_fmt_list(sorted(only_expected))}")
            if not (tier_ok and missing_ok):
                mismatches += 1

            print(
                f"{r.client_id:<7} {r.tier:<5} "
                f"{('OK' if missing_ok else 'DIFF'):<14} "
                f"{c.submission_status:<24} {_fmt_list(notes):<30}"
            )

        # -- Persistence counts -------------------------------------------
        print("\n" + "=" * 92)
        print("TURSO PERSISTENCE (row counts)")
        print("=" * 92)
        for table in ("clients", "documents", "reminder_log", "review_queue"):
            print(f"  {table:<14} {storage.count(table)} rows")

        # -- Guardrail assertion ------------------------------------------
        tier3_with_message = [
            r for r in results
            if r.tier >= 3 and r.reminder_text is not None
        ]
        print("\n" + "=" * 92)
        print("GUARDRAIL CHECK")
        print("=" * 92)
        if tier3_with_message:
            print(f"  FAIL: {len(tier3_with_message)} Tier-3 client(s) got a message!")
            mismatches += len(tier3_with_message)
        else:
            print("  PASS: no Tier-3 client received a client-facing message.")

        print("\n" + "=" * 92)
        if mismatches == 0:
            print("RESULT: all tiers and missing-document flags match the CSV.")
        else:
            print(f"RESULT: {mismatches} discrepancy/ies vs CSV — see NOTES above.")
        print("=" * 92 + "\n")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one document-collection cycle.")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit one JSON object per client on stdout (logs go to stderr).",
    )
    args = parser.parse_args()

    _configure_logging(args.json)

    if not config.turso_configured():
        # Error to stderr in both modes so stdout stays clean in JSON mode.
        print(
            "ERROR: Turso is not configured. Set TURSO_DATABASE_URL and "
            "TURSO_AUTH_TOKEN in .env.",
            file=sys.stderr,
        )
        return 1

    return _run_json() if args.json else _run_human()


if __name__ == "__main__":
    raise SystemExit(main())
