"""Tests for src/pindorama/extract_native.py — Stage 4a native text extraction.

Fixtures are generated on-the-fly with pymupdf so no test PDFs ship in-repo.
The extraction logic, the DB query, the persistence layer, and the end-to-end
driver are all exercised against real (tiny) PDFs and an in-memory SQLite DB.
"""

from __future__ import annotations

import dataclasses
import sqlite3
from pathlib import Path

import pymupdf
import pytest

from pindorama import db
from pindorama.extract_native import (
    ERROR,
    METHOD_NATIVE,
    PAGE_SEPARATOR,
    SUCCESS,
    ExtractResult,
    WorkRow,
    extract_pdf,
    fetch_pending,
    run_extract,
    update_extraction,
)

# ---------------------------------------------------------------------------
# fixture helpers
# ---------------------------------------------------------------------------


def _make_native_pdf(
    path: Path,
    *,
    n_pages: int = 3,
    page_marker: str = "lorem ipsum dolor sit amet ",
) -> Path:
    """A PDF whose pages contain extractable text. Each page gets a unique marker
    so per-page splitting can be asserted against real content."""
    doc = pymupdf.open()  # type: ignore[no-untyped-call]
    try:
        for i in range(n_pages):
            page = doc.new_page()
            block = f"page-{i} {page_marker}" * 20
            rect = pymupdf.Rect(  # type: ignore[no-untyped-call]
                50, 50, page.rect.width - 50, page.rect.height - 50
            )
            page.insert_textbox(rect, block, fontsize=10)
        doc.save(path)  # type: ignore[no-untyped-call]
    finally:
        doc.close()  # type: ignore[no-untyped-call]
    return path


def _make_garbage_pdf(path: Path, *, size_bytes: int = 4096) -> Path:
    """Random bytes saved with a .pdf extension. pymupdf must reject."""
    path.write_bytes(b"NOT A PDF " * (size_bytes // 10 + 1))
    return path


def _populate_work_row(
    conn: sqlite3.Connection,
    *,
    catalog_url: str,
    file_path: Path | str | None,
    file_sha256: str | None = None,
    triage_status: str | None = "native_extractable",
    extraction_status: str | None = None,
    download_status: str = "success",
    error_log: str | None = None,
) -> int:
    """Insert one row in the works table; return its id."""
    file_path_str: str | None = str(file_path) if file_path is not None else None
    cur = conn.execute(
        "INSERT INTO works "
        "(catalog_url, file_path, file_sha256, download_status, "
        " triage_status, extraction_status, error_log) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            catalog_url,
            file_path_str,
            file_sha256,
            download_status,
            triage_status,
            extraction_status,
            error_log,
        ),
    )
    conn.commit()
    return int(cur.lastrowid or 0)


# ---------------------------------------------------------------------------
# extract_pdf
# ---------------------------------------------------------------------------


def test_extract_pdf_writes_utf8_text_for_native(tmp_path: Path) -> None:
    src = _make_native_pdf(tmp_path / "n.pdf", n_pages=3)
    dest = tmp_path / "out" / "abc.txt"
    dest.parent.mkdir(parents=True, exist_ok=True)
    result = extract_pdf(src, dest)
    assert result.extraction_status == SUCCESS
    assert result.extracted_path == str(dest)
    assert result.n_pages == 3
    assert result.error is None
    assert dest.exists()
    body = dest.read_text(encoding="utf-8")
    assert "lorem ipsum" in body
    assert result.n_chars == len(body)


def test_extract_pdf_separates_pages_with_form_feed(tmp_path: Path) -> None:
    src = _make_native_pdf(tmp_path / "n.pdf", n_pages=4)
    dest = tmp_path / "out.txt"
    result = extract_pdf(src, dest)
    assert result.extraction_status == SUCCESS
    body = dest.read_text(encoding="utf-8")
    chunks = body.split(PAGE_SEPARATOR)
    # n_pages chunks → n_pages-1 form feeds.
    assert len(chunks) == 4
    for i, chunk in enumerate(chunks):
        assert f"page-{i}" in chunk


def test_extract_pdf_writes_no_bom(tmp_path: Path) -> None:
    src = _make_native_pdf(tmp_path / "n.pdf", n_pages=1)
    dest = tmp_path / "out.txt"
    extract_pdf(src, dest)
    raw = dest.read_bytes()
    # UTF-8 BOM is EF BB BF; we want a clean UTF-8 file.
    assert not raw.startswith(b"\xef\xbb\xbf")


def test_extract_pdf_handles_missing_source(tmp_path: Path) -> None:
    src = tmp_path / "nope.pdf"
    dest = tmp_path / "out.txt"
    result = extract_pdf(src, dest)
    assert result.extraction_status == ERROR
    assert result.error is not None and result.error.startswith("file_missing:")
    assert result.extracted_path is None
    assert not dest.exists()


def test_extract_pdf_handles_unopenable_file(tmp_path: Path) -> None:
    src = _make_garbage_pdf(tmp_path / "g.pdf")
    dest = tmp_path / "out.txt"
    result = extract_pdf(src, dest)
    assert result.extraction_status == ERROR
    assert result.error is not None and result.error.startswith("open_failed:")
    assert result.extracted_path is None
    assert not dest.exists()


def test_extract_pdf_does_not_strip_whitespace(tmp_path: Path) -> None:
    """Downstream dedup needs page boundaries to remain stable; we don't
    re-shape extracted text in this stage."""
    src = _make_native_pdf(tmp_path / "n.pdf", n_pages=2)
    dest = tmp_path / "out.txt"
    extract_pdf(src, dest)
    body = dest.read_text(encoding="utf-8")
    # pymupdf appends trailing newlines per page; we keep them as-is.
    chunks = body.split(PAGE_SEPARATOR)
    assert len(chunks) == 2
    assert any(c.endswith("\n") for c in chunks)


# ---------------------------------------------------------------------------
# fetch_pending / update_extraction
# ---------------------------------------------------------------------------


def test_fetch_pending_returns_only_native_with_null_extraction() -> None:
    conn = db.connect(":memory:")
    try:
        _populate_work_row(
            conn,
            catalog_url="u/1",
            file_path="/x/a.pdf",
            file_sha256="a" * 64,
            triage_status="native_extractable",
            extraction_status=None,
        )
        _populate_work_row(
            conn,
            catalog_url="u/2",
            file_path="/x/b.pdf",
            file_sha256="b" * 64,
            triage_status="needs_ocr",
            extraction_status=None,
        )
        _populate_work_row(
            conn,
            catalog_url="u/3",
            file_path="/x/c.pdf",
            file_sha256="c" * 64,
            triage_status="native_extractable",
            extraction_status="success",
        )
        _populate_work_row(
            conn,
            catalog_url="u/4",
            file_path="/x/d.pdf",
            file_sha256="d" * 64,
            triage_status="corrupted",
            extraction_status=None,
        )
        rows = fetch_pending(conn)
        assert [r.file_path for r in rows] == ["/x/a.pdf"]
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
                file_sha256=str(i) * 64,
                triage_status="native_extractable",
            )
        rows = fetch_pending(conn, limit=2)
        assert len(rows) == 2
    finally:
        conn.close()


def test_update_extraction_success_writes_method_and_path() -> None:
    conn = db.connect(":memory:")
    try:
        wid = _populate_work_row(
            conn,
            catalog_url="u/1",
            file_path="/x/a.pdf",
            file_sha256="a" * 64,
        )
        result = ExtractResult(
            extraction_status=SUCCESS,
            extracted_path="/scratch/extracted_text/aaa.txt",
            n_pages=42,
            n_chars=12345,
            duration_ms=300,
            error=None,
        )
        update_extraction(conn, wid, result)
        row = conn.execute(
            "SELECT extraction_status, extraction_method, extracted_path, "
            "       token_count, error_log "
            "FROM works WHERE id=?",
            (wid,),
        ).fetchone()
        assert row[0] == SUCCESS
        assert row[1] == METHOD_NATIVE
        assert row[2] == "/scratch/extracted_text/aaa.txt"
        assert row[3] is None
        assert row[4] is None
    finally:
        conn.close()


def test_update_extraction_success_resets_token_count() -> None:
    """Stage 6 backfills token_count; success here must clear any prior value."""
    conn = db.connect(":memory:")
    try:
        wid = _populate_work_row(
            conn,
            catalog_url="u/1",
            file_path="/x/a.pdf",
            file_sha256="a" * 64,
        )
        # Pretend a prior pass left a stale token_count.
        conn.execute("UPDATE works SET token_count=999 WHERE id=?", (wid,))
        conn.commit()
        result = ExtractResult(
            extraction_status=SUCCESS,
            extracted_path="/x/a.txt",
            n_pages=1,
            n_chars=10,
            duration_ms=1,
            error=None,
        )
        update_extraction(conn, wid, result)
        row = conn.execute("SELECT token_count FROM works WHERE id=?", (wid,)).fetchone()
        assert row[0] is None
    finally:
        conn.close()


def test_update_extraction_error_records_error_log() -> None:
    conn = db.connect(":memory:")
    try:
        wid = _populate_work_row(
            conn,
            catalog_url="u/1",
            file_path="/x/a.pdf",
            file_sha256="a" * 64,
        )
        result = ExtractResult(
            extraction_status=ERROR,
            extracted_path=None,
            n_pages=0,
            n_chars=0,
            duration_ms=5,
            error="open_failed:RuntimeError:boom",
        )
        update_extraction(conn, wid, result)
        row = conn.execute(
            "SELECT extraction_status, extraction_method, extracted_path, error_log "
            "FROM works WHERE id=?",
            (wid,),
        ).fetchone()
        assert row[0] == ERROR
        assert row[1] is None
        assert row[2] is None
        assert row[3] == "open_failed:RuntimeError:boom"
    finally:
        conn.close()


def test_update_extraction_error_preserves_existing_error_log_when_none() -> None:
    conn = db.connect(":memory:")
    try:
        wid = _populate_work_row(
            conn,
            catalog_url="u/1",
            file_path="/x/a.pdf",
            file_sha256="a" * 64,
            error_log="prior_failure",
        )
        result = ExtractResult(
            extraction_status=ERROR,
            extracted_path=None,
            n_pages=0,
            n_chars=0,
            duration_ms=1,
            error=None,  # ← preserve the prior log instead of overwriting with NULL
        )
        update_extraction(conn, wid, result)
        row = conn.execute("SELECT error_log FROM works WHERE id=?", (wid,)).fetchone()
        assert row[0] == "prior_failure"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# run_extract end-to-end
# ---------------------------------------------------------------------------


def test_run_extract_persists_files_and_db(tmp_path: Path) -> None:
    pdf = _make_native_pdf(tmp_path / "n.pdf", n_pages=2)
    db_path = tmp_path / "metadata.sqlite"
    extract_dir = tmp_path / "extracted_text"

    sha = "a" * 64
    conn = db.connect(db_path)
    try:
        _populate_work_row(conn, catalog_url="u/1", file_path=pdf, file_sha256=sha)
    finally:
        conn.close()

    rc = run_extract(db_path, extract_dir=extract_dir)
    assert rc == 0

    out = extract_dir / f"{sha}.txt"
    assert out.exists()
    body = out.read_text(encoding="utf-8")
    assert "page-0" in body and "page-1" in body

    conn = db.connect(db_path)
    try:
        row = conn.execute(
            "SELECT extraction_status, extraction_method, extracted_path FROM works"
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == SUCCESS
    assert row[1] == METHOD_NATIVE
    assert row[2] == str(out)


def test_run_extract_is_idempotent(tmp_path: Path) -> None:
    pdf = _make_native_pdf(tmp_path / "n.pdf", n_pages=1)
    db_path = tmp_path / "metadata.sqlite"
    extract_dir = tmp_path / "extracted_text"
    sha = "a" * 64

    conn = db.connect(db_path)
    try:
        _populate_work_row(conn, catalog_url="u/1", file_path=pdf, file_sha256=sha)
    finally:
        conn.close()

    assert run_extract(db_path, extract_dir=extract_dir) == 0
    assert run_extract(db_path, extract_dir=extract_dir) == 0  # second pass is a no-op

    conn = db.connect(db_path)
    try:
        rows = conn.execute("SELECT extraction_status, extraction_method FROM works").fetchall()
    finally:
        conn.close()
    assert rows == [(SUCCESS, METHOD_NATIVE)]


def test_run_extract_dry_run_writes_no_files_and_no_db(tmp_path: Path) -> None:
    pdf = _make_native_pdf(tmp_path / "n.pdf", n_pages=1)
    db_path = tmp_path / "metadata.sqlite"
    extract_dir = tmp_path / "extracted_text"
    sha = "a" * 64

    conn = db.connect(db_path)
    try:
        _populate_work_row(conn, catalog_url="u/1", file_path=pdf, file_sha256=sha)
    finally:
        conn.close()

    rc = run_extract(db_path, extract_dir=extract_dir, dry_run=True)
    assert rc == 0
    assert not extract_dir.exists()  # mkdir is gated on dry_run

    conn = db.connect(db_path)
    try:
        row = conn.execute("SELECT extraction_status FROM works WHERE catalog_url='u/1'").fetchone()
    finally:
        conn.close()
    assert row[0] is None


def test_run_extract_handles_null_file_path(tmp_path: Path) -> None:
    db_path = tmp_path / "metadata.sqlite"
    extract_dir = tmp_path / "extracted_text"

    conn = db.connect(db_path)
    try:
        _populate_work_row(conn, catalog_url="u/x", file_path=None, file_sha256="a" * 64)
    finally:
        conn.close()

    rc = run_extract(db_path, extract_dir=extract_dir)
    assert rc == 0

    conn = db.connect(db_path)
    try:
        row = conn.execute(
            "SELECT extraction_status, error_log FROM works WHERE catalog_url='u/x'"
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == ERROR
    assert row[1] == "missing_file_path_or_sha"


def test_run_extract_handles_null_file_sha256(tmp_path: Path) -> None:
    pdf = _make_native_pdf(tmp_path / "n.pdf", n_pages=1)
    db_path = tmp_path / "metadata.sqlite"
    extract_dir = tmp_path / "extracted_text"

    conn = db.connect(db_path)
    try:
        _populate_work_row(conn, catalog_url="u/x", file_path=pdf, file_sha256=None)
    finally:
        conn.close()

    rc = run_extract(db_path, extract_dir=extract_dir)
    assert rc == 0

    conn = db.connect(db_path)
    try:
        row = conn.execute(
            "SELECT extraction_status, error_log FROM works WHERE catalog_url='u/x'"
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == ERROR
    assert row[1] == "missing_file_path_or_sha"


def test_run_extract_continues_after_per_row_error(tmp_path: Path) -> None:
    good = _make_native_pdf(tmp_path / "good.pdf", n_pages=1)
    bad = _make_garbage_pdf(tmp_path / "bad.pdf")
    db_path = tmp_path / "metadata.sqlite"
    extract_dir = tmp_path / "extracted_text"

    conn = db.connect(db_path)
    try:
        _populate_work_row(conn, catalog_url="u/g", file_path=good, file_sha256="g" * 64)
        _populate_work_row(conn, catalog_url="u/b", file_path=bad, file_sha256="b" * 64)
    finally:
        conn.close()

    rc = run_extract(db_path, extract_dir=extract_dir)
    assert rc == 0

    conn = db.connect(db_path)
    try:
        statuses = dict(conn.execute("SELECT catalog_url, extraction_status FROM works").fetchall())
    finally:
        conn.close()
    assert statuses == {"u/g": SUCCESS, "u/b": ERROR}
    assert (extract_dir / f"{'g' * 64}.txt").exists()
    assert not (extract_dir / f"{'b' * 64}.txt").exists()


def test_run_extract_max_rows_caps_work(tmp_path: Path) -> None:
    db_path = tmp_path / "metadata.sqlite"
    extract_dir = tmp_path / "extracted_text"

    conn = db.connect(db_path)
    try:
        for i in range(3):
            pdf = _make_native_pdf(tmp_path / f"n{i}.pdf", n_pages=1)
            _populate_work_row(
                conn,
                catalog_url=f"u/{i}",
                file_path=pdf,
                file_sha256=str(i) * 64,
            )
    finally:
        conn.close()

    rc = run_extract(db_path, extract_dir=extract_dir, max_rows=2)
    assert rc == 0

    conn = db.connect(db_path)
    try:
        done = conn.execute(
            "SELECT COUNT(*) FROM works WHERE extraction_status IS NOT NULL"
        ).fetchone()[0]
        pending = conn.execute(
            "SELECT COUNT(*) FROM works WHERE extraction_status IS NULL"
        ).fetchone()[0]
    finally:
        conn.close()
    assert done == 2
    assert pending == 1


def test_run_extract_creates_extract_dir(tmp_path: Path) -> None:
    pdf = _make_native_pdf(tmp_path / "n.pdf", n_pages=1)
    db_path = tmp_path / "metadata.sqlite"
    extract_dir = tmp_path / "deep" / "nested" / "extracted_text"

    conn = db.connect(db_path)
    try:
        _populate_work_row(conn, catalog_url="u/1", file_path=pdf, file_sha256="a" * 64)
    finally:
        conn.close()

    assert not extract_dir.exists()
    run_extract(db_path, extract_dir=extract_dir)
    assert extract_dir.is_dir()


def test_run_extract_accepts_injected_rows(tmp_path: Path) -> None:
    """The injectable rows_iter exists for tests that bypass DB seeding."""
    pdf = _make_native_pdf(tmp_path / "x.pdf", n_pages=1)
    db_path = tmp_path / "metadata.sqlite"
    extract_dir = tmp_path / "extracted_text"
    injected = WorkRow(id=999, file_sha256="x" * 64, file_path=str(pdf))
    rc = run_extract(db_path, extract_dir=extract_dir, rows_iter=[injected])
    assert rc == 0
    assert (extract_dir / f"{'x' * 64}.txt").exists()


# ---------------------------------------------------------------------------
# data-class smoke tests
# ---------------------------------------------------------------------------


def test_extract_result_is_frozen() -> None:
    r = ExtractResult(
        extraction_status=SUCCESS,
        extracted_path="/x/a.txt",
        n_pages=1,
        n_chars=10,
        duration_ms=1,
        error=None,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.extraction_status = ERROR  # type: ignore[misc]


def test_workrow_has_expected_fields() -> None:
    r = WorkRow(id=1, file_sha256="abc", file_path="/x/a.pdf")
    assert r.id == 1
    assert r.file_sha256 == "abc"
    assert r.file_path == "/x/a.pdf"
