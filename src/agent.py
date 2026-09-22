"""Orchestration — the core agentic loop.

run_cycle() ties everything together for every client:

  1. load_clients()      -> upsert client state into Turso
  2. get_tier(...)       -> decide escalation tier (0-3)
  3. check_completeness  -> per-document submitted/missing/ambiguous
  4. decide the action, enforcing two HARD guardrails in code:
       * Tier 3            -> review_queue (tier_3_escalation), NEVER a
                              client-facing message. draft_reminder is
                              hard-forced to None here regardless of what
                              it returns.
       * any ambiguous doc -> review_queue (ambiguous_document),
                              regardless of tier, NEVER a client message.
  5. otherwise (Tier 0-2, everything verifiable, docs still missing)
     -> draft + log a reminder, then "send" it via a stubbed log line.

Nothing is ever actually delivered — sending is a log line only.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

from .clients import Client, load_clients
from .completeness import STATUS_AMBIGUOUS, STATUS_MISSING, check_completeness
from .reminders import draft_reminder
from .storage import Storage
from .tiers import get_tier

logger = logging.getLogger("clientdoc.agent")

# Action labels used in the summary output.
ACTION_COMPLETE = "no_action_complete"
ACTION_REMINDER = "reminder_drafted"
ACTION_ESCALATE_TIER3 = "escalated_tier3"
ACTION_REVIEW_AMBIGUOUS = "review_ambiguous_doc"


@dataclass
class CycleResult:
    client_id: str
    tier: int
    action: str
    missing_documents: List[str]
    ambiguous_documents: List[str]
    reminder_text: Optional[str] = None


def _send_stub(client: Client, message: str) -> None:
    """Stub for real delivery (email/WhatsApp). Phase 2 only logs it."""
    logger.info(
        "[SEND-STUB] would send via %s to %s: %s",
        client.last_contact_channel or "Email",
        client.contact_email or client.client_id,
        message,
    )


def process_client(client: Client, storage: Storage) -> CycleResult:
    """Run the full decision for a single client and persist the outcome."""
    tier = get_tier(client.days_since_first_request, client.reminders_sent)

    doc_statuses: Dict[str, str] = check_completeness(client, storage)
    missing = [d for d, s in doc_statuses.items() if s == STATUS_MISSING]
    ambiguous = [d for d, s in doc_statuses.items() if s == STATUS_AMBIGUOUS]

    # --- Guardrail 1: any ambiguous document -> human review, no message. ---
    if ambiguous:
        storage.add_to_review_queue(client.client_id, "ambiguous_document")
        logger.info(
            "%s: ambiguous document(s) %s -> review_queue (no client message)",
            client.client_id,
            ambiguous,
        )
        return CycleResult(
            client.client_id, tier, ACTION_REVIEW_AMBIGUOUS, missing, ambiguous
        )

    # --- Nothing outstanding: complete -> no message, no escalation. ---------
    # A client who has submitted everything needs neither a reminder nor a
    # human chase, even if enough days have elapsed to reach Tier 3.
    if not missing:
        logger.info("%s: complete, nothing to remind", client.client_id)
        return CycleResult(
            client.client_id, tier, ACTION_COMPLETE, missing, ambiguous
        )

    # --- Guardrail 2: Tier 3 -> human review, hard-block any message. --------
    if tier >= 3:
        # Even though draft_reminder returns None for tier 3, we force it
        # here so the guarantee is a code branch, not a prompt/stub detail.
        message = None  # noqa: F841 - documents the hard block
        _ = draft_reminder(client, tier)  # will be None; never sent
        storage.add_to_review_queue(client.client_id, "tier_3_escalation")
        logger.info(
            "%s: Tier 3 -> review_queue (tier_3_escalation), no client message",
            client.client_id,
        )
        return CycleResult(
            client.client_id, tier, ACTION_ESCALATE_TIER3, missing, ambiguous
        )

    # --- Tier 0-2 with missing docs: draft + log + (stub) send. --------------
    message = draft_reminder(client, tier)
    if message is None:
        # Defensive: should not happen for tier 0-2, but never send None.
        logger.warning(
            "%s: no reminder produced for tier %s; skipping send",
            client.client_id,
            tier,
        )
        return CycleResult(
            client.client_id, tier, ACTION_COMPLETE, missing, ambiguous
        )

    storage.log_reminder(client.client_id, tier, message)
    _send_stub(client, message)
    return CycleResult(
        client.client_id, tier, ACTION_REMINDER, missing, ambiguous, message
    )


def run_cycle(storage: Optional[Storage] = None) -> List[CycleResult]:
    """Run one full tracking + reminder cycle over all clients.

    If no storage is supplied, a Turso-backed Storage is created (and the
    schema ensured). All state is persisted to Turso.
    """
    owns_storage = storage is None
    if storage is None:
        storage = Storage()
    try:
        storage.init_schema()
        clients = load_clients(storage)
        results = [process_client(c, storage) for c in clients]
        return results
    finally:
        if owns_storage:
            storage.close()
