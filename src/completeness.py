"""Text extraction + completeness checking.

For a given client we compare the required-document checklist against the
PDFs that actually exist on disk, parsing each present PDF with pdfplumber
(plain-text extraction only — a text-presence check, no OCR and no field
extraction, per the stated limitation).

Per required document, the resulting status is one of:
  submitted  — a matching PDF exists and yields readable text
  missing    — no matching PDF exists for that required document
  ambiguous  — a matching PDF exists but produced no usable text
                (e.g. a scanned/photographed page we can't confidently
                 verify without OCR) or raised a parse error

All statuses are written to the `documents` table.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pdfplumber

from . import config
from .clients import Client

STATUS_SUBMITTED = "submitted"
STATUS_MISSING = "missing"
STATUS_AMBIGUOUS = "ambiguous"
STATUS_NOT_TRACKED = "not_tracked"

# The GST input/output summary is a *derived* tax figure Priya compiles from
# the collected invoices — not a source document the client uploads. In the
# dataset it is only ever listed under required_documents for GST-registered
# clients; it never appears as a submitted file, never appears in a client's
# missing_documents column, and has no entry in _manifest.csv. We therefore
# track it as a required line item but exclude it from the file-based
# submitted/missing check, matching the CSV's own ground truth.
_NON_COLLECTIBLE_RAW = ("GST input/output summary",)


def _is_collectible(label: str) -> bool:
    non_collectible = {_normalise(x) for x in _NON_COLLECTIBLE_RAW}
    return _normalise(label) not in non_collectible


def _normalise(label: str) -> str:
    """Loose normalisation so label variants compare equal.

    Lowercases, drops the GST parenthetical noise, collapses separators and
    non-alphanumerics. e.g. "GST input/output summary" -> "gstinputoutputsummary".
    """
    label = label.lower()
    label = label.replace("&", "and")
    label = re.sub(r"[^a-z0-9]+", "", label)
    return label


def _load_manifest() -> Dict[str, List[Tuple[str, str]]]:
    """Return {client_id: [(document_label, absolute_file_path), ...]}.

    The manifest lists only the documents a client actually submitted, which
    is exactly the set of files present on disk.
    """
    mapping: Dict[str, List[Tuple[str, str]]] = {}
    if not config.MANIFEST_CSV.exists():
        return mapping
    with open(config.MANIFEST_CSV, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            cid = row["client_id"].strip()
            label = row["document"].strip()
            rel = row["file"].strip()
            abs_path = str((config.CLIENT_DOCUMENTS_DIR / rel).resolve())
            mapping.setdefault(cid, []).append((label, abs_path))
    return mapping


_MANIFEST = _load_manifest()


def load_manifest(force_reload: bool = False) -> Dict[str, List[Tuple[str, str]]]:
    """Public accessor for the uploaded-document index (client_id -> files).

    Cached after first load. `force_reload=True` re-reads _manifest.csv from
    disk (useful if files changed between runs); the cached copy is what the
    completeness check consults per client.
    """
    global _MANIFEST
    if force_reload:
        _MANIFEST = _load_manifest()
    return _MANIFEST


def _pdf_has_text(path: str) -> bool:
    """True if pdfplumber extracts non-trivial text from the PDF.

    A submitted PDF with no extractable text is treated as unverifiable
    (ambiguous) rather than confidently "submitted", since without OCR we
    can't read a scanned/photographed page.
    """
    try:
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                if text.strip():
                    return True
        return False
    except Exception:
        # Unreadable / corrupt / unexpected format -> can't verify.
        return False


def _find_file_for_required(
    required_label: str, submitted: List[Tuple[str, str]]
) -> str | None:
    """Match a required-document label to a submitted file, if present."""
    target = _normalise(required_label)
    for label, path in submitted:
        if _normalise(label) == target:
            return path
    # Fall back to matching against the file name stem.
    for _label, path in submitted:
        stem = _normalise(Path(path).stem)
        if stem and (stem in target or target in stem):
            return path
    return None


def check_completeness(client: Client) -> Dict[str, Tuple[str, Optional[str]]]:
    """Extract text + compare against the required list. Does NOT write to DB.

    Returns {document_label: (status, file_path)} for the client's required
    documents. Persisting these statuses to the `documents` table is a
    separate stage (see agent.update_checklist), so this stage is pure
    text-extraction + completeness with no side effects on storage.
    """
    submitted = _MANIFEST.get(client.client_id, [])
    results: Dict[str, Tuple[str, Optional[str]]] = {}

    for required in client.required_documents:
        if not _is_collectible(required):
            # Derived summary (e.g. GST input/output) — recorded as required
            # but not counted as a collectible file, matching the dataset.
            results[required] = (STATUS_NOT_TRACKED, None)
            continue

        path = _find_file_for_required(required, submitted)
        if path is None or not Path(path).exists():
            results[required] = (STATUS_MISSING, None)
        elif _pdf_has_text(path):
            results[required] = (STATUS_SUBMITTED, path)
        else:
            # File exists but we can't read/verify it -> route to a human.
            results[required] = (STATUS_AMBIGUOUS, path)

    return results
