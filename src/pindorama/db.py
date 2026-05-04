"""SQLite schema + connection helper for the Pindorama metadata DB.

This module is intentionally minimal. It does NOT contain pipeline business
logic — those live in their per-stage modules (scrape_catalog, download_pdfs,
triage, extract_*, postprocess, package_hf). All any of them needs from here
is the shared schema and a `connect()` helper that returns a configured
sqlite3.Connection.

See ADR-0003 for the rationale of using SQLite as the single source of truth.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

# Schema is fixed by ADR-0003 (SQLite as source of truth). Do NOT edit columns
# in place — extend with a follow-up migration (a new ADR) instead. Old Parquet
# shards must remain valid against any past schema.
SCHEMA: str = """
CREATE TABLE IF NOT EXISTS works (
    id INTEGER PRIMARY KEY,
    catalog_url TEXT UNIQUE,
    pdf_url TEXT,
    title TEXT,
    author TEXT,
    year INTEGER,
    raw_metadata_json TEXT,
    scraped_at TIMESTAMP,
    download_status TEXT DEFAULT 'pending',
    file_path TEXT,
    file_sha256 TEXT,
    file_size_bytes INTEGER,
    downloaded_at TIMESTAMP,
    triage_status TEXT,
    extraction_status TEXT,
    extracted_path TEXT,
    extraction_method TEXT,
    token_count INTEGER,
    error_log TEXT
);
"""

# Helpful indices for the queries every pipeline stage actually runs.
INDICES: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS ix_works_download_status ON works(download_status);",
    "CREATE INDEX IF NOT EXISTS ix_works_triage_status   ON works(triage_status);",
    "CREATE INDEX IF NOT EXISTS ix_works_extraction_status ON works(extraction_status);",
    "CREATE INDEX IF NOT EXISTS ix_works_file_sha256     ON works(file_sha256);",
)


def connect(path: Path | str) -> sqlite3.Connection:
    """Open (and lazily create) the metadata SQLite DB at `path`.

    `path` may be `":memory:"` for tests. The connection has WAL journaling
    and foreign keys enabled. Schema and indices are created idempotently.
    """
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.executescript(SCHEMA)
    for stmt in INDICES:
        conn.execute(stmt)
    conn.commit()
    return conn
