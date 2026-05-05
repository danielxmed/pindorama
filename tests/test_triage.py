"""Tests for src/pindorama/triage.py — Stage 3 PDF triage.

Fixtures are generated on-the-fly with pymupdf so we don't ship test PDFs
in-repo. The classification logic, the DB query, and the end-to-end driver
are all exercised against real (tiny) PDFs and an in-memory SQLite DB.
"""

from __future__ import annotations

import dataclasses
import sqlite3
from pathlib import Path

import pymupdf
import pytest

from pindorama import db
from pindorama.triage import (
    CORRUPTED,
    MIN_CHARS_PER_PAGE,
    NATIVE,
    NEEDS_OCR,
    TOO_SMALL,
    TOO_SMALL_BYTES,
    TriageResult,
    WorkRow,
    classify_pdf,
    fetch_pending,
    run_triage,
    update_triage,
)

# ---------------------------------------------------------------------------
# fixture helpers
# ---------------------------------------------------------------------------


def _make_native_pdf(path: Path, *, n_pages: int = 3, chars_per_page: int = 400) -> Path:
    """A PDF whose pages contain enough extractable text to be NATIVE."""
    doc = pymupdf.open()  # type: ignore[no-untyped-call]
    try:
        # `lorem ipsum dolor sit amet ` is 27 chars; repeat to hit chars_per_page.
        block = "lorem ipsum dolor sit amet " * (max(1, chars_per_page // 27) + 1)
        for _ in range(n_pages):
            page = doc.new_page()
            # insert_textbox lays out wrapped text in a rect; insert_text would
            # truncate at the page edge. Either yields extractable get_text().
            rect = pymupdf.Rect(  # type: ignore[no-untyped-call]
                50, 50, page.rect.width - 50, page.rect.height - 50
            )
            page.insert_textbox(rect, block, fontsize=10)
        doc.save(path)  # type: ignore[no-untyped-call]
    finally:
        doc.close()  # type: ignore[no-untyped-call]
    return path


def _make_image_only_pdf(path: Path, *, n_pages: int = 3) -> Path:
    """A PDF whose pages have no text (would need OCR to be useful).

    Pages get a few filled rectangles (no text). Drawing operators inflate the
    file size above ``TOO_SMALL_BYTES`` so the page actually reaches the text
    extraction step instead of short-circuiting on ``too_small``.
    """
    doc = pymupdf.open()  # type: ignore[no-untyped-call]
    try:
        for _ in range(n_pages):
            page = doc.new_page()
            for x in range(0, 400, 20):
                page.draw_rect(
                    pymupdf.Rect(x, x, x + 60, x + 60),  # type: ignore[no-untyped-call]
                    color=(0.5, 0.5, 0.5),
                    fill=(0.5, 0.5, 0.5),
                )
        doc.save(path)  # type: ignore[no-untyped-call]
    finally:
        doc.close()  # type: ignore[no-untyped-call]
    return path


def _make_garbage_pdf(path: Path, *, size_bytes: int = 4096) -> Path:
    """Random bytes saved with a .pdf extension. pymupdf must reject."""
    path.write_bytes(b"NOT A PDF " * (size_bytes // 10 + 1))
    return path


def _make_truncated_pdf(path: Path, *, size_bytes: int = 100) -> Path:
    """Smaller than ``TOO_SMALL_BYTES`` — must classify as too_small without opening."""
    assert size_bytes < TOO_SMALL_BYTES
    path.write_bytes(b"%PDF-1.4 truncated\n" + b"x" * max(0, size_bytes - 19))
    return path


def _populate_work_row(
    conn: sqlite3.Connection,
    *,
    catalog_url: str,
    file_path: Path | str | None,
    file_sha256: str | None = "0" * 64,
    file_size_bytes: int | None = None,
    download_status: str = "success",
    triage_status: str | None = None,
    error_log: str | None = None,
) -> int:
    """Insert one row in the works table; return its id."""
    file_path_str: str | None = str(file_path) if file_path is not None else None
    if file_size_bytes is None and isinstance(file_path, Path) and file_path.exists():
        file_size_bytes = file_path.stat().st_size
    cur = conn.execute(
        "INSERT INTO works "
        "(catalog_url, file_path, file_sha256, file_size_bytes, "
        " download_status, triage_status, error_log) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            catalog_url,
            file_path_str,
            file_sha256,
            file_size_bytes,
            download_status,
            triage_status,
            error_log,
        ),
    )
    conn.commit()
    return int(cur.lastrowid or 0)


# ---------------------------------------------------------------------------
# classify_pdf
# ---------------------------------------------------------------------------


def test_classify_native_text_pdf(tmp_path: Path) -> None:
    pdf = _make_native_pdf(tmp_path / "native.pdf", n_pages=3, chars_per_page=400)
    result = classify_pdf(pdf, file_size_bytes=pdf.stat().st_size)
    assert result.triage_status == NATIVE
    assert result.sampled_pages == 3
    assert result.sampled_chars >= MIN_CHARS_PER_PAGE * result.sampled_pages
    assert result.error is None


def test_classify_image_only_pdf_routes_to_ocr(tmp_path: Path) -> None:
    pdf = _make_image_only_pdf(tmp_path / "scan.pdf", n_pages=3)
    result = classify_pdf(pdf, file_size_bytes=pdf.stat().st_size)
    assert result.triage_status == NEEDS_OCR
    assert result.sampled_pages == 3
    assert result.sampled_chars < MIN_CHARS_PER_PAGE * result.sampled_pages
    assert result.error is None


def test_classify_corrupted_garbage_file(tmp_path: Path) -> None:
    pdf = _make_garbage_pdf(tmp_path / "garbage.pdf", size_bytes=4096)
    result = classify_pdf(pdf, file_size_bytes=pdf.stat().st_size)
    assert result.triage_status == CORRUPTED
    assert result.error is not None and result.error.startswith("open_failed:")


def test_classify_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.pdf"
    result = classify_pdf(missing, file_size_bytes=None)
    assert result.triage_status == CORRUPTED
    assert result.error is not None and result.error.startswith("file_missing:")


def test_classify_truncated_short_file(tmp_path: Path) -> None:
    pdf = _make_truncated_pdf(tmp_path / "tiny.pdf", size_bytes=100)
    result = classify_pdf(pdf, file_size_bytes=pdf.stat().st_size)
    assert result.triage_status == TOO_SMALL
    assert result.error is None
    assert result.sampled_pages == 0


def test_classify_uses_passed_size_when_supplied(tmp_path: Path) -> None:
    """The DB-stored size is authoritative; we should not always re-stat."""
    pdf = _make_truncated_pdf(tmp_path / "tinier.pdf", size_bytes=50)
    # Pretend the row says 50000 bytes; classify should still try to open and
    # then fail because the on-disk content is bogus → CORRUPTED, not TOO_SMALL.
    result = classify_pdf(pdf, file_size_bytes=50_000)
    assert result.triage_status == CORRUPTED


def test_classify_samples_at_most_five_pages(tmp_path: Path) -> None:
    pdf = _make_native_pdf(tmp_path / "long.pdf", n_pages=20, chars_per_page=400)
    result = classify_pdf(pdf, file_size_bytes=pdf.stat().st_size)
    assert result.sampled_pages == 5  # SAMPLE_PAGES cap


# ---------------------------------------------------------------------------
# fetch_pending / update_triage
# ---------------------------------------------------------------------------


def test_fetch_pending_returns_only_success_and_untriaged() -> None:
    conn = db.connect(":memory:")
    try:
        _populate_work_row(conn, catalog_url="u/1", file_path="/x/a.pdf", download_status="success")
        _populate_work_row(
            conn,
            catalog_url="u/2",
            file_path="/x/b.pdf",
            download_status="success",
            triage_status="needs_ocr",
        )
        _populate_work_row(
            conn,
            catalog_url="u/3",
            file_path="/x/c.pdf",
            download_status="pending",
        )
        rows = fetch_pending(conn)
        assert len(rows) == 1
        assert rows[0].file_path == "/x/a.pdf"
    finally:
        conn.close()


def test_fetch_pending_respects_limit() -> None:
    conn = db.connect(":memory:")
    try:
        for i in range(5):
            _populate_work_row(
                conn,
                catalog_url=f"u/{i}",
                file_path=f"/x/{i}.pdf",
                download_status="success",
            )
        rows = fetch_pending(conn, limit=2)
        assert len(rows) == 2
    finally:
        conn.close()


def test_update_triage_sets_status_and_error_log() -> None:
    conn = db.connect(":memory:")
    try:
        wid = _populate_work_row(conn, catalog_url="u/1", file_path="/x/a.pdf")
        update_triage(conn, wid, NEEDS_OCR, error_log=None)
        row = conn.execute(
            "SELECT triage_status, error_log FROM works WHERE id=?", (wid,)
        ).fetchone()
        assert row[0] == NEEDS_OCR
        assert row[1] is None

        update_triage(conn, wid, CORRUPTED, error_log="open_failed:RuntimeError:boom")
        row = conn.execute(
            "SELECT triage_status, error_log FROM works WHERE id=?", (wid,)
        ).fetchone()
        assert row[0] == CORRUPTED
        assert row[1] == "open_failed:RuntimeError:boom"
    finally:
        conn.close()


def test_update_triage_preserves_existing_error_log_when_none() -> None:
    conn = db.connect(":memory:")
    try:
        wid = _populate_work_row(
            conn,
            catalog_url="u/1",
            file_path="/x/a.pdf",
            error_log="prior_failure",
        )
        update_triage(conn, wid, NATIVE, error_log=None)
        row = conn.execute(
            "SELECT triage_status, error_log FROM works WHERE id=?", (wid,)
        ).fetchone()
        assert row[0] == NATIVE
        assert row[1] == "prior_failure"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# run_triage end-to-end
# ---------------------------------------------------------------------------


def test_run_triage_classifies_a_mix(tmp_path: Path) -> None:
    native = _make_native_pdf(tmp_path / "n.pdf", n_pages=2, chars_per_page=400)
    scan = _make_image_only_pdf(tmp_path / "s.pdf", n_pages=2)
    bad = _make_garbage_pdf(tmp_path / "g.pdf", size_bytes=4096)
    tiny = _make_truncated_pdf(tmp_path / "t.pdf", size_bytes=80)
    missing = tmp_path / "missing.pdf"  # never created

    db_path = tmp_path / "metadata.sqlite"
    conn = db.connect(db_path)
    try:
        _populate_work_row(conn, catalog_url="u/n", file_path=native)
        _populate_work_row(conn, catalog_url="u/s", file_path=scan)
        _populate_work_row(conn, catalog_url="u/g", file_path=bad)
        _populate_work_row(conn, catalog_url="u/t", file_path=tiny)
        _populate_work_row(conn, catalog_url="u/m", file_path=missing)
    finally:
        conn.close()

    rc = run_triage(db_path)
    assert rc == 0

    conn = db.connect(db_path)
    try:
        statuses = dict(conn.execute("SELECT catalog_url, triage_status FROM works").fetchall())
    finally:
        conn.close()
    assert statuses == {
        "u/n": NATIVE,
        "u/s": NEEDS_OCR,
        "u/g": CORRUPTED,
        "u/t": TOO_SMALL,
        "u/m": CORRUPTED,
    }


def test_run_triage_is_idempotent(tmp_path: Path) -> None:
    native = _make_native_pdf(tmp_path / "n.pdf", n_pages=2, chars_per_page=400)
    db_path = tmp_path / "metadata.sqlite"

    conn = db.connect(db_path)
    try:
        _populate_work_row(conn, catalog_url="u/n", file_path=native)
    finally:
        conn.close()

    assert run_triage(db_path) == 0
    assert run_triage(db_path) == 0  # second pass: nothing to do, exits clean

    conn = db.connect(db_path)
    try:
        rows = conn.execute("SELECT triage_status FROM works").fetchall()
    finally:
        conn.close()
    assert rows == [(NATIVE,)]


def test_run_triage_dry_run_does_not_persist(tmp_path: Path) -> None:
    native = _make_native_pdf(tmp_path / "n.pdf", n_pages=2, chars_per_page=400)
    db_path = tmp_path / "metadata.sqlite"

    conn = db.connect(db_path)
    try:
        _populate_work_row(conn, catalog_url="u/n", file_path=native)
    finally:
        conn.close()

    rc = run_triage(db_path, dry_run=True)
    assert rc == 0

    conn = db.connect(db_path)
    try:
        row = conn.execute("SELECT triage_status FROM works").fetchone()
    finally:
        conn.close()
    assert row[0] is None  # not persisted under --dry-run


def test_run_triage_handles_null_file_path(tmp_path: Path) -> None:
    db_path = tmp_path / "metadata.sqlite"
    conn = db.connect(db_path)
    try:
        _populate_work_row(conn, catalog_url="u/x", file_path=None)
    finally:
        conn.close()

    rc = run_triage(db_path)
    assert rc == 0

    conn = db.connect(db_path)
    try:
        row = conn.execute("SELECT triage_status, error_log FROM works").fetchone()
    finally:
        conn.close()
    assert row[0] == CORRUPTED
    assert row[1] == "no_file_path"


def test_run_triage_max_rows_caps_work(tmp_path: Path) -> None:
    db_path = tmp_path / "metadata.sqlite"
    fixtures = []
    conn = db.connect(db_path)
    try:
        for i in range(3):
            pdf = _make_native_pdf(tmp_path / f"n{i}.pdf", n_pages=1, chars_per_page=400)
            fixtures.append(pdf)
            _populate_work_row(conn, catalog_url=f"u/{i}", file_path=pdf)
    finally:
        conn.close()

    rc = run_triage(db_path, max_rows=2)
    assert rc == 0

    conn = db.connect(db_path)
    try:
        triaged = conn.execute(
            "SELECT COUNT(*) FROM works WHERE triage_status IS NOT NULL"
        ).fetchone()[0]
        untriaged = conn.execute(
            "SELECT COUNT(*) FROM works WHERE triage_status IS NULL"
        ).fetchone()[0]
    finally:
        conn.close()
    assert triaged == 2
    assert untriaged == 1


# ---------------------------------------------------------------------------
# data-class smoke tests
# ---------------------------------------------------------------------------


def test_triage_result_is_frozen() -> None:
    r = TriageResult(
        triage_status=NATIVE,
        sampled_pages=3,
        sampled_chars=900,
        duration_ms=42,
        error=None,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.triage_status = NEEDS_OCR  # type: ignore[misc]


def test_workrow_has_expected_fields() -> None:
    r = WorkRow(id=1, file_sha256="abc", file_path="/x/a.pdf", file_size_bytes=1234)
    assert r.id == 1
    assert r.file_sha256 == "abc"
    assert r.file_path == "/x/a.pdf"
    assert r.file_size_bytes == 1234


def test_run_triage_accepts_injected_rows(tmp_path: Path) -> None:
    """The injectable rows_iter exists for tests that bypass DB seeding."""
    pdf = _make_native_pdf(tmp_path / "x.pdf", n_pages=1, chars_per_page=400)
    db_path = tmp_path / "metadata.sqlite"
    # No row in DB; pass via rows_iter — the driver should still attempt to
    # update_triage, but with no matching id the UPDATE is a no-op (which is
    # fine; we just verify the driver doesn't crash on injected rows).
    injected = WorkRow(
        id=999,
        file_sha256="x",
        file_path=str(pdf),
        file_size_bytes=pdf.stat().st_size,
    )
    rc = run_triage(db_path, rows_iter=[injected])
    assert rc == 0
