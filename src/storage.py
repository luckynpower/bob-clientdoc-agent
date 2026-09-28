"""Turso (libSQL) persistence layer.

Every tier decision, drafted reminder, document flag, and review-queue
entry is written here — nothing important is left as a local print.

Tables (minimum required by the spec):
  clients        client_id, client_name, client_type, gst_registered,
                 submission_status, days_since_first_request,
                 reminders_sent, last_contact_channel
  documents      client_id, document_label, status, file_path
  reminder_log   client_id, tier, message_text, timestamp
  review_queue   client_id, reason, created_at, resolved
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Optional

import libsql_client

from . import config


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _review_status(item: dict) -> str:
    """Human-facing status for a review_queue row.

    open             -> not yet decided (resolved = 0, decision NULL)
    approved         -> decision = "approved"
    dismissed        -> decision = "dismissed"
    resolved_legacy  -> resolved = 1 but no decision (pre-approve/dismiss row)
    """
    decision = item.get("decision")
    if decision in ("approved", "dismissed"):
        return decision
    if item.get("resolved"):
        return "resolved_legacy"
    return "open"


class Storage:
    """Thin wrapper around a synchronous libSQL client."""

    def __init__(self, url: Optional[str] = None, auth_token: Optional[str] = None):
        self.url = url if url is not None else config.TURSO_DATABASE_URL
        self.auth_token = auth_token if auth_token is not None else config.TURSO_AUTH_TOKEN
        if not self.url:
            raise RuntimeError(
                "TURSO_DATABASE_URL is not set. Fill it in .env before running."
            )
        # libsql_client speaks https/wss; normalise the libsql:// scheme.
        url = self.url
        if url.startswith("libsql://"):
            url = "https://" + url[len("libsql://"):]
        self._client = libsql_client.create_client_sync(
            url=url, auth_token=self.auth_token or None
        )

    # -- low level -----------------------------------------------------------
    def execute(self, sql: str, args: Optional[Iterable[Any]] = None):
        return self._client.execute(sql, list(args) if args is not None else None)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- schema --------------------------------------------------------------
    def _column_exists(self, table: str, column: str) -> bool:
        rs = self.execute(f"PRAGMA table_info({table})")
        # PRAGMA table_info rows: (cid, name, type, notnull, dflt_value, pk)
        return any(str(row[1]) == column for row in rs.rows)

    def _ensure_column(self, table: str, column: str, coltype: str) -> None:
        """Add a column if it doesn't already exist (safe, non-destructive)."""
        if not self._column_exists(table, column):
            self.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")

    def init_schema(self) -> None:
        self.execute(
            """
            CREATE TABLE IF NOT EXISTS clients (
                client_id                TEXT PRIMARY KEY,
                client_name              TEXT,
                client_type              TEXT,
                gst_registered           INTEGER,
                submission_status        TEXT,
                days_since_first_request INTEGER,
                reminders_sent           INTEGER,
                last_contact_channel     TEXT
            )
            """
        )
        self.execute(
            """
            CREATE TABLE IF NOT EXISTS documents (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id      TEXT NOT NULL,
                document_label TEXT NOT NULL,
                status         TEXT NOT NULL,   -- submitted / missing / ambiguous
                file_path      TEXT,
                UNIQUE(client_id, document_label)
            )
            """
        )
        self.execute(
            """
            CREATE TABLE IF NOT EXISTS reminder_log (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id    TEXT NOT NULL,
                tier         INTEGER NOT NULL,
                message_text TEXT NOT NULL,
                timestamp    TEXT NOT NULL,
                source       TEXT
            )
            """
        )
        # Safe migration for tables created before `source` existed: add the
        # column only if it isn't already present (ADD COLUMN is non-
        # destructive; existing rows get NULL source).
        self._ensure_column("reminder_log", "source", "TEXT")
        self.execute(
            """
            CREATE TABLE IF NOT EXISTS review_queue (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id  TEXT NOT NULL,
                reason     TEXT NOT NULL,       -- tier_3_escalation / ambiguous_document
                created_at TEXT NOT NULL,
                resolved   INTEGER NOT NULL DEFAULT 0,
                detail     TEXT,                -- fingerprint of what triggered the flag
                decision   TEXT,                -- approved / dismissed (NULL = undecided)
                note       TEXT,                -- manager's free-text note
                decided_at TEXT,                -- ISO-8601 UTC when decided
                decided_by TEXT                 -- authenticated identity that decided
            )
            """
        )
        # Safe migrations for review_queue tables created before these columns
        # existed. ADD COLUMN is non-destructive; existing rows get NULL. An old
        # resolved=1 row therefore has decision IS NULL and is surfaced as
        # "resolved (legacy)" rather than an approve/dismiss.
        for col in ("detail", "decision", "note", "decided_at", "decided_by"):
            self._ensure_column("review_queue", col, "TEXT")

    # -- clients -------------------------------------------------------------
    def upsert_client(
        self,
        client_id: str,
        client_name: str,
        client_type: str,
        gst_registered: bool,
        submission_status: str,
        days_since_first_request: int,
        reminders_sent: int,
        last_contact_channel: str,
    ) -> None:
        self.execute(
            """
            INSERT INTO clients (
                client_id, client_name, client_type, gst_registered,
                submission_status, days_since_first_request, reminders_sent,
                last_contact_channel
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(client_id) DO UPDATE SET
                client_name              = excluded.client_name,
                client_type              = excluded.client_type,
                gst_registered           = excluded.gst_registered,
                submission_status        = excluded.submission_status,
                days_since_first_request = excluded.days_since_first_request,
                reminders_sent           = excluded.reminders_sent,
                last_contact_channel     = excluded.last_contact_channel
            """,
            [
                client_id,
                client_name,
                client_type,
                1 if gst_registered else 0,
                submission_status,
                days_since_first_request,
                reminders_sent,
                last_contact_channel,
            ],
        )

    # -- documents -----------------------------------------------------------
    def upsert_document(
        self,
        client_id: str,
        document_label: str,
        status: str,
        file_path: Optional[str] = None,
    ) -> None:
        self.execute(
            """
            INSERT INTO documents (client_id, document_label, status, file_path)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(client_id, document_label) DO UPDATE SET
                status    = excluded.status,
                file_path = excluded.file_path
            """,
            [client_id, document_label, status, file_path],
        )

    # -- reminder log --------------------------------------------------------
    def log_reminder(self, client_id: str, tier: int, message_text: str) -> None:
        self.execute(
            "INSERT INTO reminder_log (client_id, tier, message_text, timestamp)"
            " VALUES (?, ?, ?, ?)",
            [client_id, tier, message_text, _now_iso()],
        )

    def reminder_exists(self, client_id: str, tier: int) -> bool:
        """Read-only: has a reminder already been logged for (client_id, tier)?

        Lets callers skip drafting entirely on re-runs (no gateway call) when a
        reminder for this client+tier is already recorded. `log_reminder_once`
        remains the authoritative write guard.
        """
        rs = self.execute(
            "SELECT COUNT(*) FROM reminder_log WHERE client_id = ? AND tier = ?",
            [client_id, tier],
        )
        return int(rs.rows[0][0]) > 0

    def log_reminder_once(
        self,
        client_id: str,
        tier: int,
        message_text: str,
        source: Optional[str] = None,
    ) -> bool:
        """Idempotent reminder log keyed on (client_id, tier).

        Records at most one reminder per client per tier: if a row already
        exists for this client_id + tier, nothing is inserted and False is
        returned. Deliberately does NOT key on message_text, because the
        wording changes once draft_reminder() is backed by the LLM gateway —
        re-running the cycle must not create a second row just because the
        generated text differs.

        `source` records where the wording came from ("llm" or
        "template_fallback").

        This caps client-facing reminders at one per tier (0, 1, 2), i.e. at
        most 3 lifetime, which is consistent with the "3+ reminders -> Tier 3"
        escalation rule (Tier 3 itself sends no reminder).

        Returns True if a new row was inserted, False if one already existed.
        """
        rs = self.execute(
            "SELECT COUNT(*) FROM reminder_log WHERE client_id = ? AND tier = ?",
            [client_id, tier],
        )
        if int(rs.rows[0][0]) > 0:
            return False
        self.execute(
            "INSERT INTO reminder_log (client_id, tier, message_text, timestamp, source)"
            " VALUES (?, ?, ?, ?, ?)",
            [client_id, tier, message_text, _now_iso(), source],
        )
        return True

    # -- review queue --------------------------------------------------------
    def add_to_review_queue(
        self, client_id: str, reason: str, detail: Optional[str] = None
    ) -> None:
        """Queue a client for human review.

        `detail` is a small, stable fingerprint of *what* triggered the flag
        (e.g. sorted ambiguous document labels, or the tier number). It lets a
        genuinely new problem re-queue while a resolved/dismissed one stays
        quiet.

        Skip re-queueing when:
          * an OPEN row with the same (client_id, reason) already exists, OR
          * an already-DECIDED row (approved OR dismissed) with the same
            (client_id, reason, detail) exists — the manager has already ruled
            on this exact situation, whether by taking it over (approved) or
            judging it a false alarm (dismissed). A CHANGED detail (new
            missing/ambiguous document, or a different tier) is NOT suppressed
            and will re-queue.
        """
        # Already open for this (client, reason)? Never duplicate.
        rs = self.execute(
            "SELECT COUNT(*) FROM review_queue"
            " WHERE client_id = ? AND reason = ? AND resolved = 0",
            [client_id, reason],
        )
        if int(rs.rows[0][0]) > 0:
            return

        # Already decided (approved or dismissed) for the SAME detail?
        # Treat as still-handled and don't re-queue.
        rs = self.execute(
            "SELECT COUNT(*) FROM review_queue"
            " WHERE client_id = ? AND reason = ?"
            "   AND decision IN ('approved', 'dismissed')"
            "   AND ((detail IS NULL AND ? IS NULL) OR detail = ?)",
            [client_id, reason, detail, detail],
        )
        if int(rs.rows[0][0]) > 0:
            return

        self.execute(
            "INSERT INTO review_queue (client_id, reason, created_at, resolved, detail)"
            " VALUES (?, ?, ?, 0, ?)",
            [client_id, reason, _now_iso(), detail],
        )

    def has_open_review_item(self, client_id: str) -> bool:
        """Read-only: does this client have any undecided/open review item?

        Used by the agent to avoid drafting/sending a reminder while a case is
        awaiting human decision.
        """
        rs = self.execute(
            "SELECT COUNT(*) FROM review_queue WHERE client_id = ? AND resolved = 0",
            [client_id],
        )
        return int(rs.rows[0][0]) > 0

    # Outcome of a decide_review_item attempt.
    DECIDE_OK = "ok"
    DECIDE_INVALID = "invalid_decision"
    DECIDE_NOT_FOUND = "not_found"
    DECIDE_ALREADY = "already_decided"

    def decide_review_item(
        self,
        review_id: int,
        decision: str,
        note: Optional[str],
        decided_by: str,
    ) -> str:
        """Approve or dismiss ONE open review item. The only write here.

        Rules:
          * decision must be 'approved' or 'dismissed' (else DECIDE_INVALID).
          * item must exist (else DECIDE_NOT_FOUND).
          * item must be open: resolved = 0 AND decision IS NULL. A second
            decision is rejected (DECIDE_ALREADY) — the first decision stands.
          * writes only decision, note, decided_at, decided_by, resolved=1.
            No deletes, nothing else touched.

        Returns one of DECIDE_OK / DECIDE_INVALID / DECIDE_NOT_FOUND /
        DECIDE_ALREADY.
        """
        if decision not in ("approved", "dismissed"):
            return self.DECIDE_INVALID

        rs = self.execute(
            "SELECT resolved, decision FROM review_queue WHERE id = ?",
            [review_id],
        )
        if not rs.rows:
            return self.DECIDE_NOT_FOUND
        resolved, existing_decision = rs.rows[0][0], rs.rows[0][1]
        if int(resolved) == 1 or existing_decision is not None:
            return self.DECIDE_ALREADY

        self.execute(
            "UPDATE review_queue"
            " SET decision = ?, note = ?, decided_at = ?, decided_by = ?, resolved = 1"
            " WHERE id = ? AND resolved = 0 AND decision IS NULL",
            [decision, note, _now_iso(), decided_by, review_id],
        )
        return self.DECIDE_OK

    # -- convenience readers (handy for the summary script / debugging) ------
    def count(self, table: str) -> int:
        rs = self.execute(f"SELECT COUNT(*) FROM {table}")
        return int(rs.rows[0][0])

    # -- Phase 3 dashboard readers (additive; read-only over Phase 2 data) ---
    def list_clients_overview(self) -> list[dict]:
        """One row per client with live state + outstanding-document counts.

        Reads clients + documents only. Tier is intentionally NOT computed
        here — the dashboard layer derives it via src.tiers.get_tier so the
        threshold logic stays in one place.
        """
        rs = self.execute(
            """
            SELECT
                c.client_id,
                c.client_name,
                c.client_type,
                c.submission_status,
                c.days_since_first_request,
                c.reminders_sent,
                c.last_contact_channel,
                COALESCE(SUM(CASE WHEN d.status = 'missing'   THEN 1 ELSE 0 END), 0) AS missing_count,
                COALESCE(SUM(CASE WHEN d.status = 'ambiguous' THEN 1 ELSE 0 END), 0) AS ambiguous_count
            FROM clients c
            LEFT JOIN documents d ON d.client_id = c.client_id
            GROUP BY c.client_id
            ORDER BY c.client_id
            """
        )
        cols = [
            "client_id",
            "client_name",
            "client_type",
            "submission_status",
            "days_since_first_request",
            "reminders_sent",
            "last_contact_channel",
            "missing_count",
            "ambiguous_count",
        ]
        return [dict(zip(cols, row)) for row in rs.rows]

    def get_client(self, client_id: str) -> Optional[dict]:
        """Single client's basic record, or None if unknown."""
        rs = self.execute(
            "SELECT client_id, client_name, client_type, submission_status,"
            " days_since_first_request, reminders_sent, last_contact_channel"
            " FROM clients WHERE client_id = ?",
            [client_id],
        )
        if not rs.rows:
            return None
        cols = [
            "client_id",
            "client_name",
            "client_type",
            "submission_status",
            "days_since_first_request",
            "reminders_sent",
            "last_contact_channel",
        ]
        return dict(zip(cols, rs.rows[0]))

    def get_reminder_history(self, client_id: str) -> list[dict]:
        """Chronological reminder_log entries for one client (oldest first).

        Includes `source` ("llm" / "template_fallback"); rows written before
        the source migration have a NULL source (surfaced as "legacy" by the
        timeline builder).
        """
        rs = self.execute(
            "SELECT id, client_id, tier, message_text, timestamp, source"
            " FROM reminder_log WHERE client_id = ?"
            " ORDER BY timestamp ASC, id ASC",
            [client_id],
        )
        cols = ["id", "client_id", "tier", "message_text", "timestamp", "source"]
        return [dict(zip(cols, row)) for row in rs.rows]

    def get_review_items_for_client(self, client_id: str) -> list[dict]:
        """All review_queue rows for one client (resolved and unresolved).

        Read-only. Used by the escalation-history timeline; unlike
        get_open_review_queue() this deliberately includes resolved items so
        the audit trail is complete.
        """
        rs = self.execute(
            "SELECT id, client_id, reason, created_at, resolved,"
            " detail, decision, note, decided_at, decided_by"
            " FROM review_queue WHERE client_id = ?"
            " ORDER BY created_at ASC, id ASC",
            [client_id],
        )
        cols = [
            "id", "client_id", "reason", "created_at", "resolved",
            "detail", "decision", "note", "decided_at", "decided_by",
        ]
        items = []
        for row in rs.rows:
            d = dict(zip(cols, row))
            d["resolved"] = bool(d["resolved"])
            items.append(d)
        return items

    def get_client_timeline(self, client_id: str) -> list[dict]:
        """Merged per-client audit timeline: reminders + review events.

        Combines reminder_log and review_queue into one list sorted newest
        first. Each entry has a normalised shape:

            kind       "reminder" | "tier_3_escalation" | "ambiguous_document"
            timestamp  ISO time the event was recorded
            tier       int for reminders; None for review events
            text       the message (reminders) or the flag reason (review)
            source     "llm" / "template_fallback" / "legacy" (reminders only;
                       "legacy" when a pre-migration row has no source);
                       None for review events
            resolved   bool for review events; None for reminders
            status     review events: "open" | "approved" | "dismissed" |
                       "resolved_legacy"; None for reminders
            note/decided_at/decided_by  review events only (may be None)

        Read-only. Sorting is by timestamp desc, then a stable tiebreak so the
        order is deterministic when timestamps collide.
        """
        entries: list[dict] = []

        for r in self.get_reminder_history(client_id):
            entries.append(
                {
                    "kind": "reminder",
                    "timestamp": r["timestamp"],
                    "tier": r["tier"],
                    "text": r["message_text"],
                    "source": r["source"] if r["source"] else "legacy",
                    "resolved": None,
                    "status": None,
                    "note": None,
                    "decided_at": None,
                    "decided_by": None,
                    "_id": r["id"],
                }
            )

        for it in self.get_review_items_for_client(client_id):
            entries.append(
                {
                    "kind": it["reason"],  # tier_3_escalation / ambiguous_document
                    "timestamp": it["created_at"],
                    "tier": None,
                    "text": it["reason"],
                    "source": None,
                    "resolved": it["resolved"],
                    "status": _review_status(it),
                    "note": it["note"],
                    "decided_at": it["decided_at"],
                    "decided_by": it["decided_by"],
                    "_id": it["id"],
                }
            )

        # Newest first. Tiebreak on (timestamp, kind, id) descending so equal
        # timestamps stay deterministic.
        entries.sort(
            key=lambda e: (e["timestamp"] or "", e["kind"], e["_id"]),
            reverse=True,
        )
        for e in entries:
            e.pop("_id", None)
        return entries

    def get_open_review_queue(self) -> list[dict]:
        """Unresolved review_queue rows joined with the client name."""
        rs = self.execute(
            """
            SELECT rq.id, rq.client_id, c.client_name, rq.reason, rq.created_at
            FROM review_queue rq
            LEFT JOIN clients c ON c.client_id = rq.client_id
            WHERE rq.resolved = 0
            ORDER BY rq.created_at ASC, rq.id ASC
            """
        )
        cols = ["id", "client_id", "client_name", "reason", "created_at"]
        return [dict(zip(cols, row)) for row in rs.rows]

    # NOTE: the old resolve_review_item() was replaced by decide_review_item()
    # (approve/dismiss). See above.

    def count_open_review_queue(self) -> int:
        rs = self.execute("SELECT COUNT(*) FROM review_queue WHERE resolved = 0")
        return int(rs.rows[0][0])
