"""Escalation tier decision logic.

Thresholds come straight from docs/week1_notes.md section 3 — they are NOT
invented here:

  | Tier | Trigger                                   |
  |------|-------------------------------------------|
  | 0    | Day 0 (initial request)                   |
  | 1    | Day 3-5, no response                      |
  | 2    | Day 7-10, still missing                   |
  | 3    | Day 14+ OR 3 reminders sent, no response  |

get_tier is a pure function of (days_since_first_request, reminders_sent).
Whether a reminder is actually appropriate (e.g. the client is already
Complete) is decided by the orchestration layer, not here.
"""
from __future__ import annotations

# Named thresholds mirror the week1_notes escalation table.
TIER1_MIN_DAYS = 3    # gentle nudge starts at day 3-5
TIER2_MIN_DAYS = 7    # firm reminder at day 7-10
TIER3_MIN_DAYS = 14   # escalate at day 14+
TIER3_MIN_REMINDERS = 3  # ...OR after 3 reminders sent


def get_tier(days_since_first_request: int, reminders_sent: int) -> int:
    """Return the escalation tier (0-3) for the given state.

    Tier 3 is reached at 14+ days OR once 3 reminders have been sent
    (the "unresponsive / human-handoff" case).
    """
    days = int(days_since_first_request)
    reminders = int(reminders_sent)

    if days >= TIER3_MIN_DAYS or reminders >= TIER3_MIN_REMINDERS:
        return 3
    if days >= TIER2_MIN_DAYS:
        return 2
    if days >= TIER1_MIN_DAYS:
        return 1
    return 0
