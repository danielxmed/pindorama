"""Stage 4a — extract text from native (non-OCR) PDFs with pymupdf.

For every row with ``triage_status='native_extractable' AND extraction_status IS NULL``:

- Open the PDF at ``row.file_path`` with pymupdf.
- Pull each page's text via ``page.get_text("text")``.
- Concatenate pages with ``\\f`` (form feed) so downstream consumers can
  resplit page-by-page.
- Write the result UTF-8 (no BOM) to ``<extract_dir>/<file_sha256>.txt``.
- On success: ``extraction_status='success'``, ``extraction_method='native'``,
  ``extracted_path=<absolute_path>``, ``token_count=NULL``.
- On any pymupdf or IO error: ``extraction_status='error'``, ``error_log``
  populated; do not crash the run, move on to the next row.

Idempotent: re-runs only touch rows still pending (``extraction_status IS NULL``).
``token_count`` is intentionally left ``NULL`` — Stage 6 backfills it against
the configured tokenizer.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pymupdf

from pindorama import db, paths

if TYPE_CHECKING:
    from collections.abc import Iterable

LOG = logging.getLogger("pindorama.extract_native")

# Page break marker. Form feed (U+000C) is a non-printing ASCII control
# character that almost never appears in extracted text, so a downstream
# ``text.split("\f")`` safely recovers per-page chunks.
PAGE_SEPARATOR = "\f"

# Extraction states. Mirrored in tests/test_extract_native.py — keep in sync.
SUCCESS = "success"
ERROR = "error"

# Method tag persisted to ``works.extraction_method`` on success. ``ocr_dots`` /
# ``ocr_chandra`` will be the values used by Stages 4b / 4c.
METHOD_NATIVE = "native"


@dataclass(frozen=True)
class ExtractResult:
    """Outcome of extracting one PDF, ready for ``UPDATE works SET extraction_status=...``."""

    extraction_status: str
    extracted_path: str | None
    n_pages: int
    n_chars: int
    duration_ms: int
    error: str | None  # populated when ``extraction_status='error'``; None on success


@dataclass(frozen=True)
class WorkRow:
    """Subset of the ``works`` row that native extraction actually needs."""

    id: int
    file_sha256: str | None
    file_path: str | None


def _ms_since(start_ns: int) -> int:
    return (time.monotonic_ns() - start_ns) // 1_000_000


def extract_pdf(src: Path, dest: Path) -> ExtractResult:
    """Extract text from one PDF and write it to ``dest``. No DB IO.

    The caller is responsible for ensuring ``dest.parent`` exists; this keeps
    filesystem writes loud and intentional (cf. ``paths.py`` docstring).
    """
    start_ns = time.monotonic_ns()

    if not src.exists():
        return ExtractResult(
            extraction_status=ERROR,
            extracted_path=None,
            n_pages=0,
            n_chars=0,
            duration_ms=_ms_since(start_ns),
            error=f"file_missing:{src}",
        )

    try:
        doc = pymupdf.open(src)  # type: ignore[no-untyped-call]
    except Exception as exc:
        return ExtractResult(
            extraction_status=ERROR,
            extracted_path=None,
            n_pages=0,
            n_chars=0,
            duration_ms=_ms_since(start_ns),
            error=f"open_failed:{type(exc).__name__}:{exc}",
        )

    try:
        page_texts: list[str] = []
        n_pages = doc.page_count
        for i in range(n_pages):
            try:
                text = doc[i].get_text("text")  # type: ignore[no-untyped-call]
            except Exception as exc:
                return ExtractResult(
                    extraction_status=ERROR,
                    extracted_path=None,
                    n_pages=i,
                    n_chars=sum(len(t) for t in page_texts),
                    duration_ms=_ms_since(start_ns),
                    error=f"page_get_text_failed:page={i}:{type(exc).__name__}:{exc}",
                )
            page_texts.append(text if isinstance(text, str) else "")

        body = PAGE_SEPARATOR.join(page_texts)
        try:
            dest.write_text(body, encoding="utf-8")
        except OSError as exc:
            return ExtractResult(
                extraction_status=ERROR,
                extracted_path=None,
                n_pages=n_pages,
                n_chars=len(body),
                duration_ms=_ms_since(start_ns),
                error=f"write_failed:{type(exc).__name__}:{exc}",
            )

        return ExtractResult(
            extraction_status=SUCCESS,
            extracted_path=str(dest),
            n_pages=n_pages,
            n_chars=len(body),
            duration_ms=_ms_since(start_ns),
            error=None,
        )
    finally:
        doc.close()  # type: ignore[no-untyped-call]


def fetch_pending(conn: sqlite3.Connection, *, limit: int | None = None) -> list[WorkRow]:
    """Return rows where triage said ``native_extractable`` but extraction hasn't run yet."""
    sql = (
        "SELECT id, file_sha256, file_path "
        "FROM works "
        "WHERE triage_status='native_extractable' AND extraction_status IS NULL "
        "ORDER BY id"
    )
    params: tuple[Any, ...] = ()
    if limit is not None:
        sql += " LIMIT ?"
        params = (limit,)
    return [WorkRow(*row) for row in conn.execute(sql, params).fetchall()]


def update_extraction(
    conn: sqlite3.Connection,
    work_id: int,
    result: ExtractResult,
) -> None:
    """Persist one extraction outcome.

    Success path resets ``token_count`` to ``NULL`` so Stage 6 unambiguously
    knows it has backfilling to do. Error path leaves ``extraction_method``
    and ``extracted_path`` untouched (both ``NULL`` from the schema default)
    and only records the failure in ``error_log`` (preserving any prior value
    via ``COALESCE``). Each call is its own transaction so a SLURM kill loses
    ≤1 row.
    """
    with conn:
        if result.extraction_status == SUCCESS:
            conn.execute(
                "UPDATE works SET extraction_status=?, extraction_method=?, "
                "extracted_path=?, token_count=NULL "
                "WHERE id=?",
                (SUCCESS, METHOD_NATIVE, result.extracted_path, work_id),
            )
        else:
            conn.execute(
                "UPDATE works SET extraction_status=?, error_log=COALESCE(?, error_log) WHERE id=?",
                (ERROR, result.error, work_id),
            )


def _now_iso_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _configure_logging() -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    LOG.handlers.clear()
    LOG.addHandler(handler)
    LOG.setLevel(logging.INFO)
    LOG.propagate = False


def _log(action: str, status: str, **extra: Any) -> None:
    LOG.info(
        json.dumps(
            {
                "ts": _now_iso_utc(),
                "level": "INFO",
                "stage": "extract_native",
                "doc_id": None,
                "action": action,
                "status": status,
                "extra": extra or None,
            },
            ensure_ascii=False,
        )
    )


def _log_row(row: WorkRow, result: ExtractResult) -> None:
    LOG.info(
        json.dumps(
            {
                "ts": _now_iso_utc(),
                "level": "INFO" if result.error is None else "WARNING",
                "stage": "extract_native",
                "doc_id": row.file_sha256,
                "action": "done",
                "status": "ok" if result.error is None else "error",
                "duration_ms": result.duration_ms,
                "extra": {
                    "extraction_status": result.extraction_status,
                    "n_pages": result.n_pages,
                    "n_chars": result.n_chars,
                    "extracted_path": result.extracted_path,
                    "error": result.error,
                },
            },
            ensure_ascii=False,
        )
    )


def run_extract(
    db_path: Path | str,
    *,
    extract_dir: Path,
    max_rows: int | None = None,
    dry_run: bool = False,
    rows_iter: Iterable[WorkRow] | None = None,
) -> int:
    """End-to-end driver. Returns a Unix-style exit code (0 ok, non-zero failure).

    ``rows_iter`` is exposed for tests; production callers pass ``None`` so we
    fetch pending rows from the DB.
    """
    if not dry_run:
        extract_dir.mkdir(parents=True, exist_ok=True)

    conn = db.connect(db_path)
    try:
        if rows_iter is not None:
            pending_list = list(rows_iter)
        else:
            pending_list = fetch_pending(conn, limit=max_rows)
        _log(
            "start",
            "ok",
            pending=len(pending_list),
            dry_run=dry_run,
            extract_dir=str(extract_dir),
        )

        counts: dict[str, int] = {}
        for row in pending_list:
            file_path = Path(row.file_path) if row.file_path else None
            if file_path is None or row.file_sha256 is None:
                result = ExtractResult(
                    extraction_status=ERROR,
                    extracted_path=None,
                    n_pages=0,
                    n_chars=0,
                    duration_ms=0,
                    error="missing_file_path_or_sha",
                )
            else:
                dest = extract_dir / f"{row.file_sha256}.txt"
                if dry_run:
                    # Mirror what production would do, but without touching disk.
                    result = ExtractResult(
                        extraction_status=SUCCESS,
                        extracted_path=str(dest),
                        n_pages=0,
                        n_chars=0,
                        duration_ms=0,
                        error=None,
                    )
                else:
                    result = extract_pdf(file_path, dest)
            counts[result.extraction_status] = counts.get(result.extraction_status, 0) + 1
            _log_row(row, result)
            if not dry_run:
                update_extraction(conn, row.id, result)

        _log("summary", "ok", counts=counts, dry_run=dry_run)
        return 0
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pindorama.extract_native")
    parser.add_argument(
        "--db",
        default=None,
        help="Path to metadata.sqlite. Defaults to pindorama.paths.default().metadata_db.",
    )
    parser.add_argument(
        "--extract-dir",
        default=None,
        help=(
            "Output dir for <sha>.txt files. Defaults to pindorama.paths.default().extracted_text."
        ),
    )
    parser.add_argument(
        "--max-rows",
        type=int,
        default=None,
        help="Hard cap on pending rows to extract. Default: all.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log intended work, but do not write text files or persist DB updates.",
    )
    args = parser.parse_args(argv)

    _configure_logging()
    layout = paths.default()
    db_path = Path(args.db) if args.db else layout.metadata_db
    extract_dir = Path(args.extract_dir) if args.extract_dir else layout.extracted_text
    return run_extract(
        db_path,
        extract_dir=extract_dir,
        max_rows=args.max_rows,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    sys.exit(main())
