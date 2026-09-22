"""Client record loading.

Reads data/synthetic_clients.csv once and upserts every record into the
Turso `clients` table. Also exposes a lightweight Client dataclass used by
the rest of the loop.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from typing import List

from . import config
from .storage import Storage


def _split_docs(cell: str) -> List[str]:
    """Split a semicolon-separated document list into clean labels.

    "(none)" and empty cells become an empty list. Stray spaces around the
    separators (the CSV has a few, e.g. "Expense receipts ; GST...") are
    trimmed so labels compare cleanly.
    """
    cell = (cell or "").strip()
    if not cell or cell.lower() == "(none)":
        return []
    return [part.strip() for part in cell.split(";") if part.strip()]


def _parse_bool(value: str) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


@dataclass
class Client:
    client_id: str
    client_name: str
    client_type: str
    gst_registered: bool
    required_documents: List[str] = field(default_factory=list)
    submitted_documents: List[str] = field(default_factory=list)
    missing_documents: List[str] = field(default_factory=list)
    submission_status: str = ""
    days_since_first_request: int = 0
    reminders_sent: int = 0
    last_contact_channel: str = ""
    contact_email: str = ""


def read_clients_csv() -> List[Client]:
    """Parse the CSV into Client objects (no DB writes)."""
    clients: List[Client] = []
    with open(config.CLIENTS_CSV, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            clients.append(
                Client(
                    client_id=row["client_id"].strip(),
                    client_name=row["client_name"].strip(),
                    client_type=row["client_type"].strip(),
                    gst_registered=_parse_bool(row["gst_registered"]),
                    required_documents=_split_docs(row["required_documents"]),
                    submitted_documents=_split_docs(row["submitted_documents"]),
                    missing_documents=_split_docs(row["missing_documents"]),
                    submission_status=row["submission_status"].strip(),
                    days_since_first_request=int(row["days_since_first_request"]),
                    reminders_sent=int(row["reminders_sent"]),
                    last_contact_channel=row["last_contact_channel"].strip(),
                    contact_email=row.get("contact_email", "").strip(),
                )
            )
    return clients


def load_clients(storage: Storage) -> List[Client]:
    """Read the CSV once and upsert every record into `clients`."""
    clients = read_clients_csv()
    for c in clients:
        storage.upsert_client(
            client_id=c.client_id,
            client_name=c.client_name,
            client_type=c.client_type,
            gst_registered=c.gst_registered,
            submission_status=c.submission_status,
            days_since_first_request=c.days_since_first_request,
            reminders_sent=c.reminders_sent,
            last_contact_channel=c.last_contact_channel,
        )
    return clients
