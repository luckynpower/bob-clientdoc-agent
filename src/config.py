"""Configuration and path resolution for the Phase 2 agent.

Loads credentials from .env (values are stripped of stray surrounding
whitespace, since the provided .env has a leading space after `=`) and
resolves the on-disk data locations. The client_documents tree in this
repo is nested one level deeper than the spec assumed, so paths are
resolved defensively rather than hardcoded.
"""
from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dotenv is a declared dependency
    load_dotenv = None


# ---------------------------------------------------------------------------
# Project paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DOCS_DIR = PROJECT_ROOT / "docs"

CLIENTS_CSV = DATA_DIR / "synthetic_clients.csv"


def _resolve_client_documents_dir() -> Path:
    """Find the directory that actually holds the per-client PDF folders.

    The repo nests it as data/client_documents/client_documents/, but we
    fall back to data/client_documents/ so the code keeps working if the
    tree is later flattened to match the spec.
    """
    nested = DATA_DIR / "client_documents" / "client_documents"
    flat = DATA_DIR / "client_documents"
    if (nested / "_manifest.csv").exists():
        return nested
    if (flat / "_manifest.csv").exists():
        return flat
    # Default to the nested layout that ships with the repo.
    return nested if nested.exists() else flat


CLIENT_DOCUMENTS_DIR = _resolve_client_documents_dir()
MANIFEST_CSV = CLIENT_DOCUMENTS_DIR / "_manifest.csv"


# ---------------------------------------------------------------------------
# Environment / credentials
# ---------------------------------------------------------------------------
def _load_env() -> None:
    if load_dotenv is not None:
        load_dotenv(PROJECT_ROOT / ".env")


def _clean(value: str | None) -> str:
    return (value or "").strip()


_load_env()

TURSO_DATABASE_URL = _clean(os.getenv("TURSO_DATABASE_URL"))
TURSO_AUTH_TOKEN = _clean(os.getenv("TURSO_AUTH_TOKEN"))
APP_ENV = _clean(os.getenv("APP_ENV")) or "development"


def turso_configured() -> bool:
    """True when both Turso credentials are present."""
    return bool(TURSO_DATABASE_URL and TURSO_AUTH_TOKEN)
