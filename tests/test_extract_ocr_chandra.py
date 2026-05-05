"""Tests for src/pindorama/extract_ocr_chandra.py — Stage 4b chandra OCR.

The real chandra-ocr model is never loaded in tests (heavy, GPU-only).
Instead we exercise the orchestration through a deterministic
``FakeOCREngine`` that returns canned strings per image. PDF fixtures are
generated on the fly with pymupdf so no test data ships in-repo.
"""

from __future__ import annotations

import dataclasses
import sqlite3
from collections.abc import Sequence
from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from pindorama import db
from pindorama.extract_ocr_chandra import (
    DEFAULT_DPI,
    DEFAULT_PAGE_BATCH,
    DEFAULT_PROMPT_TYPE,
    ERROR,
    METHOD_OCR_CHANDRA,
    PAGE_SEPARATOR,
    SUCCESS,
    ExtractResult,
    OCREngine,
    WorkRow,
    extract_pdf,
    fetch_pending,
    render_pdf_pages,
    run_extract,
    update_extraction,
)

# ---------------------------------------------------------------------------
# fixtures + helpers
# ---------------------------------------------------------------------------


def _make_pdf(path: Path, *, n_pages: int = 3) -> Path:
    """Build a tiny multi-page PDF — pymupdf can render any of these to a pixmap.

    The page content is irrelevant: OCR is faked, so the bytes the renderer
    emits are only consumed to drive the ``render_pdf_pages`` plumbing. We
    insert visible text anyway so the smoke test on the cluster has something
    to look at when it dumps a page.
    """
    doc = pymupdf.open()  # type: ignore[no-untyped-call]
    try:
        for i in range(n_pages):
            page = doc.new_page()
            rect = pymupdf.Rect(  # type: ignore[no-untyped-call]
                50, 50, page.rect.width - 50, page.rect.height - 50
            )
            page.insert_textbox(rect, f"page-{i} sample", fontsize=12)
        doc.save(path)  # type: ignore[no-untyped-call]
    finally:
        doc.close()  # type: ignore[no-untyped-call]
    return path


def _make_garbage_pdf(path: Path) -> Path:
    path.write_bytes(b"NOT A PDF " * 512)
    return path


class FakeOCREngine:
    """Deterministic substitute for ``VLLMOCREngine``.

    Records every call so tests can assert on batch sizes (e.g. that the
    driver respects ``page_batch_size``). Returns ``f"OCR<i>"`` for the i-th
    image *across the run* so per-page output is stable and unique.
    """

    def __init__(self) -> None:
        self.batches: list[int] = []
        self._counter = 0

    def transcribe(self, images: Sequence[Image.Image]) -> list[str]:
        self.batches.append(len(images))
        out: list[str] = []
        for _ in images:
            out.append(f"OCR{self._counter}")
            self._counter += 1
        return out


class CountingFactory:
    """Engine factory that counts builds + returns a fresh ``FakeOCREngine``."""

    def __init__(self) -> None:
        self.calls = 0
        self.last: FakeOCREngine | None = None

    def __call__(self) -> OCREngine:
        self.calls += 1
        self.last = FakeOCREngine()
        return self.last


class RaisingEngine:
    """Engine that always raises — used to assert per-row error isolation."""

    def __init__(self, message: str = "kaboom") -> None:
        self._message = message

    def transcribe(self, images: Sequence[Image.Image]) -> list[str]:
        raise RuntimeError(self._message)


class MismatchEngine:
    """Engine that returns one fewer string than requested."""

    def transcribe(self, images: Sequence[Image.Image]) -> list[str]:
        return ["x"] * max(0, len(images) - 1)


def _populate_work_row(
    conn: sqlite3.Connection,
    *,
    catalog_url: str,
    file_path: Path | str | None,
    file_sha256: str | None = None,
    triage_status: str | None = "needs_ocr",
    extraction_status: str | None = None,
    download_status: str = "success",
    error_log: str | None = None,
) -> int:
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
# render_pdf_pages
# ---------------------------------------------------------------------------


def test_render_pdf_pages_returns_pil_image_per_page(tmp_path: Path) -> None:
    src = _make_pdf(tmp_path / "n.pdf", n_pages=3)
    images = render_pdf_pages(src, dpi=72)
    assert len(images) == 3
    for img in images:
        assert isinstance(img, Image.Image)
        assert img.mode == "RGB"
        # 72 dpi against a default A4 page → not zero pixels.
        assert img.width > 0 and img.height > 0


def test_render_pdf_pages_dpi_scales_pixel_count(tmp_path: Path) -> None:
    src = _make_pdf(tmp_path / "n.pdf", n_pages=1)
    low = render_pdf_pages(src, dpi=72)
    high = render_pdf_pages(src, dpi=200)
    assert high[0].width > low[0].width
    assert high[0].height > low[0].height


# ---------------------------------------------------------------------------
# extract_pdf
# ---------------------------------------------------------------------------


def test_extract_pdf_writes_concatenated_text(tmp_path: Path) -> None:
    src = _make_pdf(tmp_path / "n.pdf", n_pages=3)
    dest = tmp_path / "out" / "abc.txt"
    dest.parent.mkdir(parents=True, exist_ok=True)
    engine = FakeOCREngine()
    result = extract_pdf(src, dest, engine, dpi=72)
    assert result.extraction_status == SUCCESS
    assert result.error is None
    assert result.extracted_path == str(dest)
    assert result.n_pages == 3
    body = dest.read_text(encoding="utf-8")
    chunks = body.split(PAGE_SEPARATOR)
    assert chunks == ["OCR0", "OCR1", "OCR2"]
    assert result.n_chars == len(body)


def test_extract_pdf_writes_no_bom(tmp_path: Path) -> None:
    src = _make_pdf(tmp_path / "n.pdf", n_pages=1)
    dest = tmp_path / "out.txt"
    extract_pdf(src, dest, FakeOCREngine(), dpi=72)
    raw = dest.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")


def test_extract_pdf_respects_page_batch_size(tmp_path: Path) -> None:
    src = _make_pdf(tmp_path / "n.pdf", n_pages=5)
    dest = tmp_path / "out.txt"
    engine = FakeOCREngine()
    result = extract_pdf(src, dest, engine, dpi=72, page_batch_size=2)
    assert result.extraction_status == SUCCESS
    # 5 pages with batch=2 → batches of 2, 2, 1.
    assert engine.batches == [2, 2, 1]


def test_extract_pdf_handles_missing_source(tmp_path: Path) -> None:
    src = tmp_path / "nope.pdf"
    dest = tmp_path / "out.txt"
    result = extract_pdf(src, dest, FakeOCREngine(), dpi=72)
    assert result.extraction_status == ERROR
    assert result.error is not None and result.error.startswith("file_missing:")
    assert not dest.exists()


def test_extract_pdf_handles_unrenderable_file(tmp_path: Path) -> None:
    src = _make_garbage_pdf(tmp_path / "g.pdf")
    dest = tmp_path / "out.txt"
    result = extract_pdf(src, dest, FakeOCREngine(), dpi=72)
    assert result.extraction_status == ERROR
    assert result.error is not None and result.error.startswith("render_failed:")
    assert not dest.exists()


def test_extract_pdf_handles_engine_exception(tmp_path: Path) -> None:
    src = _make_pdf(tmp_path / "n.pdf", n_pages=2)
    dest = tmp_path / "out.txt"
    result = extract_pdf(src, dest, RaisingEngine("boom"), dpi=72)
    assert result.extraction_status == ERROR
    assert result.error is not None and result.error.startswith("transcribe_failed:RuntimeError:")
    assert "boom" in result.error
    assert not dest.exists()


def test_extract_pdf_handles_engine_output_mismatch(tmp_path: Path) -> None:
    src = _make_pdf(tmp_path / "n.pdf", n_pages=3)
    dest = tmp_path / "out.txt"
    result = extract_pdf(src, dest, MismatchEngine(), dpi=72, page_batch_size=3)
    assert result.extraction_status == ERROR
    assert result.error is not None and result.error.startswith("engine_output_mismatch:")
    assert not dest.exists()


# ---------------------------------------------------------------------------
# fetch_pending / update_extraction
# ---------------------------------------------------------------------------


def test_fetch_pending_returns_only_needs_ocr_with_null_extraction() -> None:
    conn = db.connect(":memory:")
    try:
        _populate_work_row(
            conn,
            catalog_url="u/1",
            file_path="/x/a.pdf",
            file_sha256="a" * 64,
            triage_status="needs_ocr",
            extraction_status=None,
        )
        _populate_work_row(
            conn,
            catalog_url="u/2",
            file_path="/x/b.pdf",
            file_sha256="b" * 64,
            triage_status="native_extractable",
            extraction_status=None,
        )
        _populate_work_row(
            conn,
            catalog_url="u/3",
            file_path="/x/c.pdf",
            file_sha256="c" * 64,
            triage_status="needs_ocr",
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
                triage_status="needs_ocr",
            )
        rows = fetch_pending(conn, limit=2)
        assert len(rows) == 2
    finally:
        conn.close()


def test_update_extraction_success_writes_method_ocr_dots() -> None:
    conn = db.connect(":memory:")
    try:
        wid = _populate_work_row(
            conn, catalog_url="u/1", file_path="/x/a.pdf", file_sha256="a" * 64
        )
        result = ExtractResult(
            extraction_status=SUCCESS,
            extracted_path="/scratch/extracted_text/aaa.txt",
            n_pages=10,
            n_chars=4321,
            duration_ms=2200,
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
        assert row[1] == METHOD_OCR_CHANDRA
        assert row[2] == "/scratch/extracted_text/aaa.txt"
        assert row[3] is None  # token_count must stay NULL for Stage 6.
        assert row[4] is None
    finally:
        conn.close()


def test_update_extraction_success_resets_token_count() -> None:
    conn = db.connect(":memory:")
    try:
        wid = _populate_work_row(
            conn, catalog_url="u/1", file_path="/x/a.pdf", file_sha256="a" * 64
        )
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
            conn, catalog_url="u/1", file_path="/x/a.pdf", file_sha256="a" * 64
        )
        result = ExtractResult(
            extraction_status=ERROR,
            extracted_path=None,
            n_pages=0,
            n_chars=0,
            duration_ms=5,
            error="render_failed:RuntimeError:boom",
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
        assert row[3] == "render_failed:RuntimeError:boom"
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
            error=None,
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
    pdf = _make_pdf(tmp_path / "n.pdf", n_pages=2)
    db_path = tmp_path / "metadata.sqlite"
    extract_dir = tmp_path / "extracted_text"

    sha = "a" * 64
    conn = db.connect(db_path)
    try:
        _populate_work_row(conn, catalog_url="u/1", file_path=pdf, file_sha256=sha)
    finally:
        conn.close()

    rc = run_extract(db_path, extract_dir=extract_dir, engine=FakeOCREngine(), dpi=72)
    assert rc == 0

    out = extract_dir / f"{sha}.txt"
    assert out.exists()
    body = out.read_text(encoding="utf-8")
    assert body.split(PAGE_SEPARATOR) == ["OCR0", "OCR1"]

    conn = db.connect(db_path)
    try:
        row = conn.execute(
            "SELECT extraction_status, extraction_method, extracted_path FROM works"
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == SUCCESS
    assert row[1] == METHOD_OCR_CHANDRA
    assert row[2] == str(out)


def test_run_extract_is_idempotent(tmp_path: Path) -> None:
    pdf = _make_pdf(tmp_path / "n.pdf", n_pages=1)
    db_path = tmp_path / "metadata.sqlite"
    extract_dir = tmp_path / "extracted_text"
    sha = "a" * 64

    conn = db.connect(db_path)
    try:
        _populate_work_row(conn, catalog_url="u/1", file_path=pdf, file_sha256=sha)
    finally:
        conn.close()

    factory = CountingFactory()
    assert run_extract(db_path, extract_dir=extract_dir, engine_factory=factory, dpi=72) == 0
    assert run_extract(db_path, extract_dir=extract_dir, engine_factory=factory, dpi=72) == 0

    conn = db.connect(db_path)
    try:
        rows = conn.execute("SELECT extraction_status, extraction_method FROM works").fetchall()
    finally:
        conn.close()
    assert rows == [(SUCCESS, METHOD_OCR_CHANDRA)]
    # Second pass had no pending rows → factory must not have been called again.
    assert factory.calls == 1


def test_run_extract_dry_run_skips_engine_and_state(tmp_path: Path) -> None:
    pdf = _make_pdf(tmp_path / "n.pdf", n_pages=1)
    db_path = tmp_path / "metadata.sqlite"
    extract_dir = tmp_path / "extracted_text"

    conn = db.connect(db_path)
    try:
        _populate_work_row(conn, catalog_url="u/1", file_path=pdf, file_sha256="a" * 64)
    finally:
        conn.close()

    factory = CountingFactory()
    rc = run_extract(
        db_path,
        extract_dir=extract_dir,
        engine_factory=factory,
        dry_run=True,
        dpi=72,
    )
    assert rc == 0
    assert not extract_dir.exists()  # mkdir is gated on dry_run.
    assert factory.calls == 0  # engine must not be built in dry-run.

    conn = db.connect(db_path)
    try:
        row = conn.execute("SELECT extraction_status FROM works WHERE catalog_url='u/1'").fetchone()
    finally:
        conn.close()
    assert row[0] is None


def test_run_extract_does_not_build_engine_when_no_pending_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "metadata.sqlite"
    extract_dir = tmp_path / "extracted_text"

    factory = CountingFactory()
    rc = run_extract(db_path, extract_dir=extract_dir, engine_factory=factory, dpi=72)
    assert rc == 0
    assert factory.calls == 0


def test_run_extract_does_not_build_engine_for_metadata_only_errors(tmp_path: Path) -> None:
    """Rows missing file_path/sha never need the engine — keep it cold."""
    db_path = tmp_path / "metadata.sqlite"
    extract_dir = tmp_path / "extracted_text"
    conn = db.connect(db_path)
    try:
        _populate_work_row(conn, catalog_url="u/x", file_path=None, file_sha256="a" * 64)
    finally:
        conn.close()

    factory = CountingFactory()
    rc = run_extract(db_path, extract_dir=extract_dir, engine_factory=factory, dpi=72)
    assert rc == 0
    assert factory.calls == 0


def test_run_extract_handles_null_file_path(tmp_path: Path) -> None:
    db_path = tmp_path / "metadata.sqlite"
    extract_dir = tmp_path / "extracted_text"

    conn = db.connect(db_path)
    try:
        _populate_work_row(conn, catalog_url="u/x", file_path=None, file_sha256="a" * 64)
    finally:
        conn.close()

    rc = run_extract(db_path, extract_dir=extract_dir, engine=FakeOCREngine(), dpi=72)
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
    pdf = _make_pdf(tmp_path / "n.pdf", n_pages=1)
    db_path = tmp_path / "metadata.sqlite"
    extract_dir = tmp_path / "extracted_text"

    conn = db.connect(db_path)
    try:
        _populate_work_row(conn, catalog_url="u/x", file_path=pdf, file_sha256=None)
    finally:
        conn.close()

    rc = run_extract(db_path, extract_dir=extract_dir, engine=FakeOCREngine(), dpi=72)
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
    good = _make_pdf(tmp_path / "good.pdf", n_pages=1)
    bad = _make_garbage_pdf(tmp_path / "bad.pdf")
    db_path = tmp_path / "metadata.sqlite"
    extract_dir = tmp_path / "extracted_text"

    conn = db.connect(db_path)
    try:
        _populate_work_row(conn, catalog_url="u/g", file_path=good, file_sha256="g" * 64)
        _populate_work_row(conn, catalog_url="u/b", file_path=bad, file_sha256="b" * 64)
    finally:
        conn.close()

    rc = run_extract(db_path, extract_dir=extract_dir, engine=FakeOCREngine(), dpi=72)
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
            pdf = _make_pdf(tmp_path / f"n{i}.pdf", n_pages=1)
            _populate_work_row(conn, catalog_url=f"u/{i}", file_path=pdf, file_sha256=str(i) * 64)
    finally:
        conn.close()

    rc = run_extract(db_path, extract_dir=extract_dir, engine=FakeOCREngine(), max_rows=2, dpi=72)
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
    pdf = _make_pdf(tmp_path / "n.pdf", n_pages=1)
    db_path = tmp_path / "metadata.sqlite"
    extract_dir = tmp_path / "deep" / "nested" / "extracted_text"

    conn = db.connect(db_path)
    try:
        _populate_work_row(conn, catalog_url="u/1", file_path=pdf, file_sha256="a" * 64)
    finally:
        conn.close()

    assert not extract_dir.exists()
    run_extract(db_path, extract_dir=extract_dir, engine=FakeOCREngine(), dpi=72)
    assert extract_dir.is_dir()


def test_run_extract_accepts_injected_rows(tmp_path: Path) -> None:
    pdf = _make_pdf(tmp_path / "x.pdf", n_pages=1)
    db_path = tmp_path / "metadata.sqlite"
    extract_dir = tmp_path / "extracted_text"
    injected = WorkRow(id=999, file_sha256="x" * 64, file_path=str(pdf))
    rc = run_extract(
        db_path,
        extract_dir=extract_dir,
        engine=FakeOCREngine(),
        rows_iter=[injected],
        dpi=72,
    )
    assert rc == 0
    assert (extract_dir / f"{'x' * 64}.txt").exists()


# ---------------------------------------------------------------------------
# data-class + module smoke tests
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


def test_constants_match_protocol() -> None:
    """Sanity-check the public constants stay coherent with the schema."""
    assert PAGE_SEPARATOR == "\f"
    assert METHOD_OCR_CHANDRA == "ocr_chandra"
    assert SUCCESS == "success"
    assert ERROR == "error"
    assert DEFAULT_DPI == 200
    assert DEFAULT_PAGE_BATCH > 0
    assert DEFAULT_PROMPT_TYPE == "ocr_layout"
