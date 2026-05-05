"""Stage 3 — triage downloaded PDFs into a routing decision for Stage 4.

For every row with ``download_status='success'`` and ``triage_status IS NULL``:

- File missing on disk or unopenable → ``corrupted`` (with ``error_log`` populated).
- ``file_size_bytes`` below ``TOO_SMALL_BYTES`` (or doc has zero pages) → ``too_small``.
- Otherwise sample the first ``SAMPLE_PAGES`` pages with pymupdf; if the average
  extracted-character count per sampled page is at least ``MIN_CHARS_PER_PAGE``
  the file is ``native_extractable`` (Stage 4a will pull text directly), else
  ``needs_ocr`` (Stage 4b/4c with vLLM-backed OCR).

Idempotent: re-runs only touch rows still pending (``triage_status IS NULL``).
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

LOG = logging.getLogger("pindorama.triage")

# Files smaller than ~1 KiB are almost certainly truncated downloads and not
# worth opening. The smallest legitimate Pindorama PDF observed in the harvest
# is ~30 KiB, so this threshold has plenty of headroom.
TOO_SMALL_BYTES = 1024

# How many leading pages to sample. A 200-page scan with consistent quality
# gives the same answer at 5 pages as at 200, and dropping the long tail
# brings 2k books in well under an hour on amilan.
SAMPLE_PAGES = 5

# Average extracted chars per sampled page below which we route to OCR.
# Empirically, native-text PDFs land at ~1500-3000 chars/page; image-only
# scans land at 0-50 (page furniture). 200 sits comfortably between.
MIN_CHARS_PER_PAGE = 200

# Triage states. Mirrored in tests/test_triage.py — keep in sync.
NATIVE = "native_extractable"
NEEDS_OCR = "needs_ocr"
CORRUPTED = "corrupted"
TOO_SMALL = "too_small"


@dataclass(frozen=True)
class TriageResult:
    """Outcome of classifying one PDF, ready for ``UPDATE works SET triage_status=...``."""

    triage_status: str
    sampled_pages: int
    sampled_chars: int
    duration_ms: int
    error: str | None  # populated for ``corrupted``; None otherwise


@dataclass(frozen=True)
class WorkRow:
    """Subset of the ``works`` row that triage actually needs."""

    id: int
    file_sha256: str | None
    file_path: str | None
    file_size_bytes: int | None


def _ms_since(start_ns: int) -> int:
    return (time.monotonic_ns() - start_ns) // 1_000_000


def classify_pdf(path: Path, file_size_bytes: int | None) -> TriageResult:
    """Classify one PDF on disk. Returns the routing decision, no IO beyond reading the file."""
    start_ns = time.monotonic_ns()

    if not path.exists():
        return TriageResult(
            triage_status=CORRUPTED,
            sampled_pages=0,
            sampled_chars=0,
            duration_ms=_ms_since(start_ns),
            error=f"file_missing:{path}",
        )

    size = file_size_bytes if file_size_bytes is not None else path.stat().st_size
    if size < TOO_SMALL_BYTES:
        return TriageResult(
            triage_status=TOO_SMALL,
            sampled_pages=0,
            sampled_chars=0,
            duration_ms=_ms_since(start_ns),
            error=None,
        )

    try:
        doc = pymupdf.open(path)  # type: ignore[no-untyped-call]
    except Exception as exc:  # pymupdf raises a variety of exception types
        return TriageResult(
            triage_status=CORRUPTED,
            sampled_pages=0,
            sampled_chars=0,
            duration_ms=_ms_since(start_ns),
            error=f"open_failed:{type(exc).__name__}:{exc}",
        )

    try:
        page_count = doc.page_count
        if page_count == 0:
            return TriageResult(
                triage_status=TOO_SMALL,
                sampled_pages=0,
                sampled_chars=0,
                duration_ms=_ms_since(start_ns),
                error=None,
            )
        n_sample = min(page_count, SAMPLE_PAGES)
        chars = 0
        for i in range(n_sample):
            try:
                text = doc[i].get_text()  # type: ignore[no-untyped-call]
            except Exception as exc:
                # A single bad page does not doom the whole document. Log and skip.
                LOG.warning(
                    json.dumps(
                        {
                            "ts": _now_iso_utc(),
                            "level": "WARNING",
                            "stage": "triage",
                            "doc_id": None,
                            "action": "page_get_text_failed",
                            "status": "partial",
                            "extra": {"page_index": i, "err": f"{type(exc).__name__}:{exc}"},
                        },
                        ensure_ascii=False,
                    )
                )
                continue
            if isinstance(text, str):
                chars += len(text.strip())
        avg = chars / n_sample if n_sample else 0
        status = NATIVE if avg >= MIN_CHARS_PER_PAGE else NEEDS_OCR
        return TriageResult(
            triage_status=status,
            sampled_pages=n_sample,
            sampled_chars=chars,
            duration_ms=_ms_since(start_ns),
            error=None,
        )
    finally:
        doc.close()  # type: ignore[no-untyped-call]


def fetch_pending(conn: sqlite3.Connection, *, limit: int | None = None) -> list[WorkRow]:
    """Return rows where the download succeeded but triage hasn't run yet."""
    sql = (
        "SELECT id, file_sha256, file_path, file_size_bytes "
        "FROM works "
        "WHERE download_status='success' AND triage_status IS NULL "
        "ORDER BY id"
    )
    params: tuple[Any, ...] = ()
    if limit is not None:
        sql += " LIMIT ?"
        params = (limit,)
    return [WorkRow(*row) for row in conn.execute(sql, params).fetchall()]


def update_triage(
    conn: sqlite3.Connection,
    work_id: int,
    triage_status: str,
    error_log: str | None,
) -> None:
    """Persist one triage outcome.

    ``error_log`` is set to the new value when non-None, otherwise the existing
    ``error_log`` is preserved (``COALESCE``). Each call is its own transaction
    so a SLURM kill loses ≤1 row.
    """
    with conn:
        conn.execute(
            "UPDATE works SET triage_status=?, error_log=COALESCE(?, error_log) WHERE id=?",
            (triage_status, error_log, work_id),
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
                "stage": "triage",
                "doc_id": None,
                "action": action,
                "status": status,
                "extra": extra or None,
            },
            ensure_ascii=False,
        )
    )


def _log_row(row: WorkRow, result: TriageResult) -> None:
    LOG.info(
        json.dumps(
            {
                "ts": _now_iso_utc(),
                "level": "INFO" if result.error is None else "WARNING",
                "stage": "triage",
                "doc_id": row.file_sha256,
                "action": "done",
                "status": "ok" if result.error is None else "partial",
                "duration_ms": result.duration_ms,
                "extra": {
                    "triage_status": result.triage_status,
                    "sampled_pages": result.sampled_pages,
                    "sampled_chars": result.sampled_chars,
                    "error": result.error,
                },
            },
            ensure_ascii=False,
        )
    )


def run_triage(
    db_path: Path | str,
    *,
    max_rows: int | None = None,
    dry_run: bool = False,
    rows_iter: Iterable[WorkRow] | None = None,
) -> int:
    """End-to-end driver. Returns a Unix-style exit code (0 ok, non-zero failure).

    ``rows_iter`` is exposed for tests; production callers pass ``None`` so we
    fetch pending rows from the DB.
    """
    conn = db.connect(db_path)
    try:
        if rows_iter is not None:
            pending_list = list(rows_iter)
        else:
            pending_list = fetch_pending(conn, limit=max_rows)
        _log("start", "ok", pending=len(pending_list), dry_run=dry_run)

        counts: dict[str, int] = {}
        for row in pending_list:
            file_path = Path(row.file_path) if row.file_path else None
            if file_path is None:
                result = TriageResult(
                    triage_status=CORRUPTED,
                    sampled_pages=0,
                    sampled_chars=0,
                    duration_ms=0,
                    error="no_file_path",
                )
            else:
                result = classify_pdf(file_path, row.file_size_bytes)
            counts[result.triage_status] = counts.get(result.triage_status, 0) + 1
            _log_row(row, result)
            if not dry_run:
                update_triage(conn, row.id, result.triage_status, result.error)

        _log("summary", "ok", counts=counts, dry_run=dry_run)
        return 0
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pindorama.triage")
    parser.add_argument(
        "--db",
        default=None,
        help="Path to metadata.sqlite. Defaults to pindorama.paths.default().metadata_db.",
    )
    parser.add_argument(
        "--max-rows",
        type=int,
        default=None,
        help="Hard cap on pending rows to triage. Default: all.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Classify and log, but do not write triage_status back.",
    )
    args = parser.parse_args(argv)

    _configure_logging()
    db_path = Path(args.db) if args.db else paths.default().metadata_db
    return run_triage(db_path, max_rows=args.max_rows, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
