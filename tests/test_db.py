"""Smoke tests for src/pindorama/db.py — schema and connection helper."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pindorama import db

# (column_name, sqlite_declared_type) — must match PINDORAMA_BOOTSTRAP_PROMPT.md §2.
EXPECTED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("id", "INTEGER"),
    ("catalog_url", "TEXT"),
    ("pdf_url", "TEXT"),
    ("title", "TEXT"),
    ("author", "TEXT"),
    ("year", "INTEGER"),
    ("raw_metadata_json", "TEXT"),
    ("scraped_at", "TIMESTAMP"),
    ("download_status", "TEXT"),
    ("file_path", "TEXT"),
    ("file_sha256", "TEXT"),
    ("file_size_bytes", "INTEGER"),
    ("downloaded_at", "TIMESTAMP"),
    ("triage_status", "TEXT"),
    ("extraction_status", "TEXT"),
    ("extracted_path", "TEXT"),
    ("extraction_method", "TEXT"),
    ("token_count", "INTEGER"),
    ("error_log", "TEXT"),
)


def test_connect_returns_sqlite3_connection() -> None:
    conn = db.connect(":memory:")
    try:
        assert isinstance(conn, sqlite3.Connection)
    finally:
        conn.close()


def test_works_table_exists() -> None:
    conn = db.connect(":memory:")
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='works';"
        ).fetchone()
        assert row is not None, "works table should be created by connect()"
    finally:
        conn.close()


def test_every_expected_column_is_present_with_correct_type() -> None:
    conn = db.connect(":memory:")
    try:
        cur = conn.execute("PRAGMA table_info(works);")
        # PRAGMA table_info returns: (cid, name, type, notnull, dflt_value, pk)
        actual = {row[1]: row[2].upper() for row in cur.fetchall()}
        for column, expected_type in EXPECTED_COLUMNS:
            assert column in actual, f"missing column: {column}"
            assert actual[column] == expected_type, (
                f"column {column}: expected {expected_type}, got {actual[column]}"
            )
        # Defensive: did the schema gain any columns we did not write a test for?
        assert set(actual) == {c for c, _ in EXPECTED_COLUMNS}, (
            f"unexpected columns in works: {set(actual) - {c for c, _ in EXPECTED_COLUMNS}}"
        )
    finally:
        conn.close()


def test_download_status_default_is_pending() -> None:
    conn = db.connect(":memory:")
    try:
        # Insert a row supplying only the unique key; default should fill download_status.
        conn.execute("INSERT INTO works (catalog_url) VALUES ('https://example/a');")
        conn.commit()
        status = conn.execute(
            "SELECT download_status FROM works WHERE catalog_url='https://example/a';"
        ).fetchone()[0]
        assert status == "pending"
    finally:
        conn.close()


def test_catalog_url_is_unique() -> None:
    conn = db.connect(":memory:")
    try:
        conn.execute("INSERT INTO works (catalog_url) VALUES ('https://example/a');")
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO works (catalog_url) VALUES ('https://example/a');")
            conn.commit()
    finally:
        conn.close()


def test_journal_mode_is_wal_for_disk_dbs(tmp_path: Path) -> None:
    # Note: SQLite ignores `journal_mode=WAL` for in-memory DBs; check on disk.
    conn = db.connect(tmp_path / "metadata.sqlite")
    try:
        mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        assert mode.lower() == "wal"
    finally:
        conn.close()


def test_foreign_keys_pragma_is_on() -> None:
    conn = db.connect(":memory:")
    try:
        on = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
        assert on == 1
    finally:
        conn.close()


def test_indices_present() -> None:
    conn = db.connect(":memory:")
    try:
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'ix_works_%';"
            ).fetchall()
        }
        assert {
            "ix_works_download_status",
            "ix_works_triage_status",
            "ix_works_extraction_status",
            "ix_works_file_sha256",
        }.issubset(names)
    finally:
        conn.close()
