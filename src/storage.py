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
                timestamp    TEXT NOT NULL
            )
            """
        )
        self.execute(
            """
            CREATE TABLE IF NOT EXISTS review_queue (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id  TEXT NOT NULL,
                reason     TEXT NOT NULL,       -- tier_3_escalation / ambiguous_document
                created_at TEXT NOT NULL,
                resolved   INTEGER NOT NULL DEFAULT 0
            )
            """
        )

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

    # -- review queue --------------------------------------------------------
    def add_to_review_queue(self, client_id: str, reason: str) -> None:
        """Queue a client for human review.

        Idempotent for open items: if an unresolved entry with the same
        client_id + reason already exists, we don't add a duplicate. This
        keeps the queue clean across repeated cycles while still allowing a
        new entry once a prior one has been resolved.
        """
        rs = self.execute(
            "SELECT COUNT(*) FROM review_queue"
            " WHERE client_id = ? AND reason = ? AND resolved = 0",
            [client_id, reason],
        )
        if int(rs.rows[0][0]) > 0:
            return
        self.execute(
            "INSERT INTO review_queue (client_id, reason, created_at, resolved)"
            " VALUES (?, ?, ?, 0)",
            [client_id, reason, _now_iso()],
        )

    # -- convenience readers (handy for the summary script / debugging) ------
    def count(self, table: str) -> int:
        rs = self.execute(f"SELECT COUNT(*) FROM {table}")
        return int(rs.rows[0][0])
