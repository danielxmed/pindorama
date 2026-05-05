"""Stage 4b — OCR scanned PDFs with datalab-to/chandra-ocr-2 (HuggingFace path).

For every row with ``triage_status='needs_ocr' AND extraction_status IS NULL``:

- Open the PDF at ``row.file_path`` with pymupdf.
- Render each page to a PIL image at the configured DPI (default 200; chandra
  itself defaults to 192 internally, 200 keeps headroom for small text).
- Submit pages in fixed-size batches to chandra's ``InferenceManager`` (HF
  backend) with ``prompt_type='ocr_layout'``. Chandra returns parsed markdown
  per page with page-headers/footers stripped via ``parse_markdown(...,
  include_headers_footers=False)`` (chandra's default).
- Concatenate per-page transcriptions with ``\\f`` (form feed), the same
  separator Stage 4a uses.
- Write the result UTF-8 (no BOM) to ``<extract_dir>/<file_sha256>.txt``.
- On success: ``extraction_status='success'``, ``extraction_method='ocr_chandra'``,
  ``extracted_path=<absolute_path>``, ``token_count=NULL``.
- On any pymupdf / chandra / IO error: ``extraction_status='error'``,
  ``error_log`` populated; never crash the run, move on to the next row.

Idempotent: re-runs only touch rows still pending (``extraction_status IS NULL``).
``token_count`` stays ``NULL`` — Stage 6 backfills it against
``TucanoBR/Tucano-2b4``.

ADR-0005 explains why chandra-ocr-2 replaced the originally-planned
vllm + dots.ocr stack (vllm wheels need glibc >= 2.35; CURC Alpine is
RHEL 8.10 / glibc 2.28). Chandra's PT-BR multilingual benchmark score is
95.2%, top-tier for Portuguese-language corpora.

The chandra engine is wrapped in an ``OCREngine`` protocol so tests can
substitute a deterministic fake without importing torch/transformers.
Production callers build a ``ChandraOCREngine`` once at the start of a SLURM
job and reuse it across the whole pending set.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import pymupdf

from pindorama import db, paths

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from PIL import Image

LOG = logging.getLogger("pindorama.extract_ocr_chandra")

PAGE_SEPARATOR = "\f"

SUCCESS = "success"
ERROR = "error"

METHOD_OCR_CHANDRA = "ocr_chandra"

# 200 DPI: matches what Stage 4a's triage classifier samples PDFs at; chandra
# itself defaults to 192 DPI (`chandra.settings.IMAGE_DPI`) and the readme
# recommends ~200 DPI for OCR. Tunable via --dpi.
DEFAULT_DPI = 200

# Conservative starting batch — chandra-ocr-2 is 8B parameters in bf16, ~17 GiB
# weights. 4 pages at 200 DPI is comfortable on a 40 GiB A100.
DEFAULT_PAGE_BATCH = 4

# Chandra has a small set of valid prompt types (see chandra/prompts.py inside
# the wheel). ``ocr_layout`` returns HTML-with-bboxes that the chandra output
# parser drops headers/footers from before producing markdown — that's
# exactly the shape we want for a literary corpus. The alternative, ``ocr``,
# returns flat HTML without layout tags so the headers/footers can't be
# stripped.
DEFAULT_PROMPT_TYPE = "ocr_layout"

DEFAULT_MODEL_ID = "datalab-to/chandra-ocr-2"


@dataclass(frozen=True)
class ExtractResult:
    """Outcome of OCR-extracting one PDF, ready for the DB write."""

    extraction_status: str
    extracted_path: str | None
    n_pages: int
    n_chars: int
    duration_ms: int
    error: str | None


@dataclass(frozen=True)
class WorkRow:
    """Subset of the ``works`` row that OCR extraction needs."""

    id: int
    file_sha256: str | None
    file_path: str | None


class OCREngine(Protocol):
    """Minimal interface a tested OCR backend must satisfy.

    ``transcribe`` takes a list of PIL images (one per PDF page) and returns
    the same number of strings, one per image, in order. Implementations are
    free to batch internally; the protocol does not promise the call is a
    single backend invocation. Failures must raise — callers convert
    exceptions into per-document error rows.
    """

    def transcribe(self, images: list[Image.Image]) -> list[str]: ...


def _ms_since(start_ns: int) -> int:
    return (time.monotonic_ns() - start_ns) // 1_000_000


def gpu_total_mib() -> int | None:
    """Return the smallest total VRAM (MiB) across visible GPUs, or ``None``.

    Used to log the GPU profile we landed on (Alpine ``aa100`` nodes have both
    40 GB and 80 GB A100s — never hardcode either). Returns ``None`` when
    nvidia-smi is absent or fails.
    """
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    values = [int(line.strip()) for line in out.splitlines() if line.strip()]
    return min(values) if values else None


def render_pdf_pages(src: Path, *, dpi: int) -> list[Image.Image]:
    """Render every page of ``src`` to a PIL image at ``dpi``.

    pymupdf's native unit is 72 dpi; ``Matrix(zoom, zoom)`` with
    ``zoom = dpi / 72`` upsamples to the target DPI. Pixmaps are released
    immediately after conversion to PIL because each one holds raw RGB bytes
    (~25 MiB for an A4 page at 200 DPI) and the C-side memory does not get
    garbage-collected promptly.
    """
    from PIL import Image as _PILImage

    zoom = dpi / 72.0
    matrix = pymupdf.Matrix(zoom, zoom)  # type: ignore[no-untyped-call]
    images: list[Image.Image] = []
    with pymupdf.open(src) as doc:  # type: ignore[no-untyped-call]
        for page in doc:
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            try:
                img = _PILImage.frombytes("RGB", (pix.width, pix.height), pix.samples)
            finally:
                del pix
            images.append(img)
    return images


def extract_pdf(
    src: Path,
    dest: Path,
    engine: OCREngine,
    *,
    dpi: int = DEFAULT_DPI,
    page_batch_size: int = DEFAULT_PAGE_BATCH,
) -> ExtractResult:
    """OCR one PDF and write the concatenated text to ``dest``. No DB IO.

    Pages are batched through ``engine`` ``page_batch_size`` at a time to keep
    GPU utilization high without driving a 500-page book into one giant
    request. ``dest.parent`` must exist before calling.
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
        images = render_pdf_pages(src, dpi=dpi)
    except Exception as exc:
        return ExtractResult(
            extraction_status=ERROR,
            extracted_path=None,
            n_pages=0,
            n_chars=0,
            duration_ms=_ms_since(start_ns),
            error=f"render_failed:{type(exc).__name__}:{exc}",
        )

    n_pages = len(images)
    if n_pages == 0:
        try:
            dest.write_text("", encoding="utf-8")
        except OSError as exc:
            return ExtractResult(
                extraction_status=ERROR,
                extracted_path=None,
                n_pages=0,
                n_chars=0,
                duration_ms=_ms_since(start_ns),
                error=f"write_failed:{type(exc).__name__}:{exc}",
            )
        return ExtractResult(
            extraction_status=SUCCESS,
            extracted_path=str(dest),
            n_pages=0,
            n_chars=0,
            duration_ms=_ms_since(start_ns),
            error=None,
        )

    page_texts: list[str] = []
    try:
        for batch_start in range(0, n_pages, page_batch_size):
            batch = images[batch_start : batch_start + page_batch_size]
            outputs = engine.transcribe(batch)
            if len(outputs) != len(batch):
                return ExtractResult(
                    extraction_status=ERROR,
                    extracted_path=None,
                    n_pages=batch_start,
                    n_chars=sum(len(t) for t in page_texts),
                    duration_ms=_ms_since(start_ns),
                    error=(
                        f"engine_output_mismatch:expected={len(batch)}:"
                        f"got={len(outputs)}:batch_start={batch_start}"
                    ),
                )
            page_texts.extend(outputs)
    except Exception as exc:
        return ExtractResult(
            extraction_status=ERROR,
            extracted_path=None,
            n_pages=len(page_texts),
            n_chars=sum(len(t) for t in page_texts),
            duration_ms=_ms_since(start_ns),
            error=f"transcribe_failed:{type(exc).__name__}:{exc}",
        )

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


def fetch_pending(conn: sqlite3.Connection, *, limit: int | None = None) -> list[WorkRow]:
    """Return rows where triage said ``needs_ocr`` but extraction hasn't run."""
    sql = (
        "SELECT id, file_sha256, file_path "
        "FROM works "
        "WHERE triage_status='needs_ocr' AND extraction_status IS NULL "
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
    """Persist one OCR extraction outcome in its own transaction.

    Success path stamps ``extraction_method='ocr_chandra'`` and resets
    ``token_count=NULL`` so Stage 6's tokenizer pass has unambiguous work to
    do. Error path leaves ``extraction_method`` and ``extracted_path`` at
    their schema defaults and records the failure in ``error_log`` (preserving
    any prior value via ``COALESCE``). Per-row commit means a SLURM kill
    loses ≤1 row.
    """
    with conn:
        if result.extraction_status == SUCCESS:
            conn.execute(
                "UPDATE works SET extraction_status=?, extraction_method=?, "
                "extracted_path=?, token_count=NULL "
                "WHERE id=?",
                (SUCCESS, METHOD_OCR_CHANDRA, result.extracted_path, work_id),
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
                "stage": "extract_chandra",
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
                "stage": "extract_chandra",
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


class ChandraOCREngine:
    """Production OCR engine: chandra-ocr-2 served via the HuggingFace path.

    Constructed once per SLURM job (model load + bf16 weight transfer to GPU
    is the slow part — minutes). ``transcribe`` then drives inference over
    arbitrary batches.

    Uses chandra's ``InferenceManager(method="hf")`` which under the hood
    loads the model with ``device_map="auto"``, sets bf16 dtype, and attaches
    the AutoProcessor. The model id is read from ``CHANDRA_MODEL_CHECKPOINT``
    (an env var honored by chandra's pydantic-settings layer); we set it
    explicitly when the user passes ``--model``.
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL_ID,
        *,
        prompt_type: str = DEFAULT_PROMPT_TYPE,
    ) -> None:
        # Chandra reads its model checkpoint from settings.MODEL_CHECKPOINT,
        # which honors environment variables via pydantic-settings. Set it
        # before importing the manager so load_model() picks the override up.
        os.environ.setdefault("MODEL_CHECKPOINT", model)
        # Lazy imports keep the module importable on CPU-only nodes (login
        # nodes, validation, the per-test process). Tests inject a fake
        # engine and never touch this constructor.
        from chandra.model import InferenceManager
        from chandra.model.schema import BatchInputItem

        self._manager = InferenceManager(method="hf")
        self._BatchInputItem = BatchInputItem
        self._prompt_type = prompt_type

    def transcribe(self, images: list[Image.Image]) -> list[str]:
        """Run chandra over a batch of page images and return the markdown.

        Raises ``RuntimeError`` if any page in the batch comes back with the
        ``error=True`` flag set — callers convert that into a per-doc error
        row, which preserves the idempotent retry semantics on resubmit.
        """
        if not images:
            return []
        batch = [self._BatchInputItem(image=img, prompt_type=self._prompt_type) for img in images]
        outputs = self._manager.generate(batch)
        if len(outputs) != len(images):
            raise RuntimeError(
                f"chandra_returned_wrong_count:expected={len(images)}:got={len(outputs)}"
            )
        results: list[str] = []
        for i, out in enumerate(outputs):
            if out.error:
                raise RuntimeError(f"chandra_page_error:index={i}")
            results.append(out.markdown or "")
        return results


def _build_engine(model: str) -> OCREngine:
    return ChandraOCREngine(model=model)


def run_extract(
    db_path: Path | str,
    *,
    extract_dir: Path,
    engine_factory: Callable[[], OCREngine] | None = None,
    engine: OCREngine | None = None,
    max_rows: int | None = None,
    dry_run: bool = False,
    dpi: int = DEFAULT_DPI,
    page_batch_size: int = DEFAULT_PAGE_BATCH,
    model: str = DEFAULT_MODEL_ID,
    rows_iter: Iterable[WorkRow] | None = None,
) -> int:
    """End-to-end driver. Returns 0 on success, non-zero on a top-level fault.

    The chandra engine is expensive to construct (model load + GPU warmup is
    minutes). To keep the dry-run and test paths cheap, callers can either:

    - inject a ready-built ``engine`` (used by tests to plug in a fake), or
    - inject ``engine_factory`` (a zero-arg callable returning an engine), or
    - leave both ``None`` to get the production ``ChandraOCREngine`` lazily,
      built only when at least one row needs OCR.
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
            dpi=dpi,
            page_batch_size=page_batch_size,
            model=model,
            gpu_total_mib=gpu_total_mib(),
        )

        engine_built: OCREngine | None = engine
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
                    result = ExtractResult(
                        extraction_status=SUCCESS,
                        extracted_path=str(dest),
                        n_pages=0,
                        n_chars=0,
                        duration_ms=0,
                        error=None,
                    )
                else:
                    if engine_built is None:
                        if engine_factory is not None:
                            engine_built = engine_factory()
                        else:
                            engine_built = _build_engine(model)
                    result = extract_pdf(
                        file_path,
                        dest,
                        engine_built,
                        dpi=dpi,
                        page_batch_size=page_batch_size,
                    )
            counts[result.extraction_status] = counts.get(result.extraction_status, 0) + 1
            _log_row(row, result)
            if not dry_run:
                update_extraction(conn, row.id, result)

        _log("summary", "ok", counts=counts, dry_run=dry_run)
        return 0
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pindorama.extract_ocr_chandra")
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
        help="Hard cap on pending rows to OCR. Default: all.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=("Log intended work; do not load chandra, write text files, or persist DB updates."),
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=DEFAULT_DPI,
        help=f"Page render DPI for OCR input. Default {DEFAULT_DPI}.",
    )
    parser.add_argument(
        "--page-batch-size",
        type=int,
        default=DEFAULT_PAGE_BATCH,
        help=f"Pages per chandra batch. Default {DEFAULT_PAGE_BATCH}.",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("PINDORAMA_OCR_MODEL", DEFAULT_MODEL_ID),
        help=(
            f"HF model id (or local path) for the OCR engine. "
            f"Default {DEFAULT_MODEL_ID} (or $PINDORAMA_OCR_MODEL)."
        ),
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
        dpi=args.dpi,
        page_batch_size=args.page_batch_size,
        model=args.model,
    )


if __name__ == "__main__":
    sys.exit(main())
