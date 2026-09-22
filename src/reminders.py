"""Reminder drafting — STUBBED for Phase 2.

This is the single seam where a real Bedrock/Claude call will later live.
For now `draft_reminder(client, tier)` returns a hardcoded template string
per tier, worded to match Priya's voice and the example messages in
docs/week1_notes.md section 3.

Contract:
  * Tiers 0-2  -> a client-facing reminder string
  * Tier 3     -> None (agent must NOT contact an unresponsive client;
                  this is enforced again in run_cycle as a hard branch)

To swap in a real LLM later, only the body of `draft_reminder` changes —
callers keep the same (client, tier) -> str | None signature.
"""
from __future__ import annotations

from typing import Optional

from .clients import Client


def _format_missing(client: Client) -> str:
    if client.missing_documents:
        return ", ".join(client.missing_documents)
    return "the outstanding documents"


# Templates keyed by tier. {client} and {missing} are filled per client.
_TEMPLATES = {
    0: (
        "Hi {client}, time for our monthly close! Could you send over "
        "{missing} at your convenience? Let us know if anything's unclear. "
        "— Priya, Maple & Co Bookkeeping"
    ),
    1: (
        "Hi {client}, just a friendly reminder — we're still waiting on "
        "{missing}. No rush, just don't want it to slip! "
        "— Priya, Maple & Co Bookkeeping"
    ),
    2: (
        "Hi {client}, we still need {missing} to complete this month's "
        "bookkeeping. Delaying further will push back your financial "
        "reports — could you send these across soon? "
        "— Priya, Maple & Co Bookkeeping"
    ),
}


def draft_reminder(client: Client, tier: int) -> Optional[str]:
    """Return a templated reminder for Tiers 0-2, or None for Tier 3.

    (Stub — a real LLM call will replace the body later.)
    """
    if tier >= 3:
        # Hard guardrail: no client-facing message for an escalated client.
        return None
    template = _TEMPLATES.get(tier)
    if template is None:
        return None
    return template.format(client=client.client_name, missing=_format_missing(client))
