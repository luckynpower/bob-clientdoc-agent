"""Orchestration — the core agentic loop.

run_cycle() is ONE clean loop that runs these stages, in this order, for
every client:

  1. intake                    — load client records + their uploaded docs
  2. text extraction + completeness
                               — pdfplumber text-presence check per PDF,
                                 compared against the required-document list
                                 (GST summary is `not_tracked`, excluded).
                                 Pure: computes statuses, writes nothing.
  3. update checklist          — write the per-document statuses from stage 2
                                 to Turso (documents table). Its own stage.
  4. decide reminder tier      — deterministic Python only (tiers.get_tier),
                                 never the LLM; computed AFTER completeness
  5. decide action + enforce guardrails:
       * complete client       -> no action, even at Tier 3 by elapsed days
       * any ambiguous document -> review_queue (escalate_to_human),
                                 regardless of tier, no client message
       * Tier 3 (14+ days OR 3+ reminders) -> review_queue, no client message
       * otherwise (Tier 0-2, docs missing) -> draft + log a reminder
                                 (idempotent per client+tier), then stub-send;
                                 if already reminded at this tier, action is
                                 "none" ("already reminded at this tier")

Nothing is ever actually delivered — sending is a log line only.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .clients import Client, load_clients
from .completeness import (
    STATUS_AMBIGUOUS,
    STATUS_MISSING,
    check_completeness,
    load_manifest,
)
from .reminders import draft_reminder
from .storage import Storage
from .tiers import get_tier

logger = logging.getLogger("clientdoc.agent")

# Internal action labels (kept for the human table / backward compat).
ACTION_COMPLETE = "no_action_complete"
ACTION_REMINDER = "reminder_drafted"
ACTION_ALREADY_REMINDED = "already_reminded"
ACTION_ESCALATE_TIER3 = "escalated_tier3"
ACTION_REVIEW_AMBIGUOUS = "review_ambiguous_doc"

# Public JSON action vocabulary (per Phase-3 audit spec).
JSON_ACTION_NONE = "none"
JSON_ACTION_REMIND = "remind"
JSON_ACTION_ESCALATE = "escalate_to_human"

# Map internal actions -> the JSON action vocabulary.
_JSON_ACTION = {
    ACTION_COMPLETE: JSON_ACTION_NONE,
    ACTION_ALREADY_REMINDED: JSON_ACTION_NONE,
    ACTION_REMINDER: JSON_ACTION_REMIND,
    ACTION_ESCALATE_TIER3: JSON_ACTION_ESCALATE,
    ACTION_REVIEW_AMBIGUOUS: JSON_ACTION_ESCALATE,
}

# Type alias: {document_label: (status, file_path)}
DocStatuses = Dict[str, Tuple[str, Optional[str]]]


@dataclass
class CycleResult:
    client_id: str
    tier: int
    action: str                       # internal action label
    missing_documents: List[str]
    ambiguous_documents: List[str]
    reminder_text: Optional[str] = None
    reason: str = ""                  # short human-readable explanation

    @property
    def json_action(self) -> str:
        return _JSON_ACTION.get(self.action, JSON_ACTION_NONE)

    def to_json_dict(self) -> dict:
        """The one-object-per-client shape emitted by `run_cycle.py --json`."""
        return {
            "client_id": self.client_id,
            "missing_docs": self.missing_documents,
            "tier": self.tier,
            "action": self.json_action,
            "reason": self.reason,
        }


# ---------------------------------------------------------------------------
# Stage 1 — intake
# ---------------------------------------------------------------------------
def stage_intake(storage: Storage) -> Tuple[List[Client], Dict[str, list]]:
    """Load client records and the index of their uploaded documents.

    Client records are upserted into Turso; the manifest of uploaded files
    is loaded explicitly here (rather than relied on as an import-time side
    effect) so document intake is a visible stage of the loop.
    """
    clients = load_clients(storage)
    manifest = load_manifest()
    logger.info(
        "intake: %d clients, %d clients with uploaded documents",
        len(clients),
        len(manifest),
    )
    return clients, manifest


# ---------------------------------------------------------------------------
# Stage 2 — text extraction + completeness (pure; no DB writes)
# ---------------------------------------------------------------------------
def stage_completeness(client: Client) -> DocStatuses:
    """Extract text from each PDF and compare to the required list.

    Returns {label: (status, file_path)}. Writes nothing — persistence is
    stage 3 (update_checklist).
    """
    return check_completeness(client)


# ---------------------------------------------------------------------------
# Stage 3 — update checklist (persist stage-2 statuses to Turso)
# ---------------------------------------------------------------------------
def stage_update_checklist(
    client: Client, doc_statuses: DocStatuses, storage: Storage
) -> Tuple[List[str], List[str]]:
    """Write per-document statuses to the `documents` table.

    Returns (missing, ambiguous) label lists derived from the statuses.
    """
    missing: List[str] = []
    ambiguous: List[str] = []
    for label, (status, file_path) in doc_statuses.items():
        storage.upsert_document(client.client_id, label, status, file_path)
        if status == STATUS_MISSING:
            missing.append(label)
        elif status == STATUS_AMBIGUOUS:
            ambiguous.append(label)
    return missing, ambiguous


# ---------------------------------------------------------------------------
# Stage 5 — action decision (uses the tier from stage 4)
# ---------------------------------------------------------------------------
def _send_stub(client: Client, message: str) -> None:
    """Stub for real delivery (email/WhatsApp). Only logs it."""
    logger.info(
        "[SEND-STUB] would send via %s to %s: %s",
        client.last_contact_channel or "Email",
        client.contact_email or client.client_id,
        message,
    )


def process_client(client: Client, storage: Storage) -> CycleResult:
    """Run stages 2-5 for a single client and persist the outcome."""
    # Stage 2: text extraction + completeness (pure).
    doc_statuses = stage_completeness(client)

    # Stage 3: update checklist (persist to documents table).
    missing, ambiguous = stage_update_checklist(client, doc_statuses, storage)

    # Stage 4: decide reminder tier — deterministic, AFTER completeness.
    tier = get_tier(client.days_since_first_request, client.reminders_sent)

    # Stage 5: decide action, enforcing guardrails as hard code branches.

    # Guardrail A: any ambiguous document -> human review, no client message,
    # regardless of tier. The `detail` fingerprint is the sorted set of
    # ambiguous labels, so a newly-ambiguous different document re-queues even
    # if a prior ambiguous case for this client was dismissed.
    if ambiguous:
        detail = "; ".join(sorted(ambiguous))
        storage.add_to_review_queue(client.client_id, "ambiguous_document", detail)
        reason = (
            f"ambiguous document(s) could not be verified: "
            f"{', '.join(ambiguous)}; routed to human review"
        )
        logger.info("%s: %s", client.client_id, reason)
        return CycleResult(
            client.client_id, tier, ACTION_REVIEW_AMBIGUOUS,
            missing, ambiguous, reason=reason,
        )

    # Guardrail B: complete client -> no message, no escalation, even if
    # elapsed days would put it at Tier 3.
    if not missing:
        reason = "all required documents received; nothing outstanding"
        logger.info("%s: %s", client.client_id, reason)
        return CycleResult(
            client.client_id, tier, ACTION_COMPLETE, missing, ambiguous,
            reason=reason,
        )

    # Guardrail C: Tier 3 -> human review, hard-block any client message. The
    # `detail` fingerprint is the tier number, so a re-escalation at the same
    # tier stays suppressed once dismissed.
    if tier >= 3:
        storage.add_to_review_queue(
            client.client_id, "tier_3_escalation", str(tier)
        )
        reason = (
            "Tier 3 (14+ days or 3+ reminders) — unresponsive; "
            "routed to human review, no automated reminder"
        )
        logger.info("%s: %s", client.client_id, reason)
        return CycleResult(
            client.client_id, tier, ACTION_ESCALATE_TIER3, missing, ambiguous,
            reason=reason,
        )

    # Guardrail D: a client with an OPEN review item is awaiting a human
    # decision — do not draft or send any reminder while it sits in the queue.
    # (Read-only check; no gateway call, no write.)
    if storage.has_open_review_item(client.client_id):
        reason = "open review item awaiting human decision; no reminder sent"
        logger.info("%s: %s", client.client_id, reason)
        return CycleResult(
            client.client_id, tier, ACTION_ALREADY_REMINDED, missing, ambiguous,
            reason=reason,
        )

    # Tier 0-2 with missing docs.
    # Read-only pre-check: if a reminder for this (client, tier) already exists,
    # skip drafting entirely so a re-run makes ZERO gateway calls. The write
    # guard (log_reminder_once) still backstops any race / direct caller.
    if storage.reminder_exists(client.client_id, tier):
        reason = "already reminded at this tier"
        logger.info("%s: %s (skipped drafting; no gateway call)", client.client_id, reason)
        return CycleResult(
            client.client_id, tier, ACTION_ALREADY_REMINDED, missing, ambiguous,
            reason=reason,
        )

    # Not yet reminded at this tier: draft + log (idempotent) + stub-send.
    drafted = draft_reminder(client, tier)
    if drafted.text is None:
        # Defensive: tier 0-2 should produce text, but never send None.
        reason = (
            f"no reminder text produced for tier {tier} "
            "(gateway unavailable and no fallback); skipped send"
        )
        logger.warning("%s: %s", client.client_id, reason)
        return CycleResult(
            client.client_id, tier, ACTION_COMPLETE, missing, ambiguous,
            reason=reason,
        )

    inserted = storage.log_reminder_once(
        client.client_id, tier, drafted.text, drafted.source
    )
    if inserted:
        _send_stub(client, drafted.text)
        reason = f"Tier {tier} reminder drafted for missing: {', '.join(missing)}"
        logger.info("%s: %s", client.client_id, reason)
        return CycleResult(
            client.client_id, tier, ACTION_REMINDER, missing, ambiguous,
            reminder_text=drafted.text, reason=reason,
        )

    # Already reminded at this tier -> no new action this cycle.
    reason = "already reminded at this tier"
    logger.info("%s: %s", client.client_id, reason)
    return CycleResult(
        client.client_id, tier, ACTION_ALREADY_REMINDED, missing, ambiguous,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# The one clean loop
# ---------------------------------------------------------------------------
def run_cycle(storage: Optional[Storage] = None) -> List[CycleResult]:
    """Run one full cycle over all clients through the ordered stages.

    If no storage is supplied, a Turso-backed Storage is created (and the
    schema ensured). All state is persisted to Turso. The run is idempotent:
    running it twice in a row does not create duplicate reminder_log or
    review_queue rows, and clients already reminded at their current tier
    report action "none".
    """
    owns_storage = storage is None
    if storage is None:
        storage = Storage()
    try:
        storage.init_schema()
        # Stage 1: intake (client records + uploaded-document index).
        clients, _manifest = stage_intake(storage)
        # Stages 2-5, per client.
        return [process_client(c, storage) for c in clients]
    finally:
        if owns_storage:
            storage.close()
