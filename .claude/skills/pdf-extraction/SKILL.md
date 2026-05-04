---
name: pdf-extraction
description: Use when working with pymupdf (fitz) to read text from PDFs, render PDF pages to images for OCR input, compute the native-vs-OCR triage score, or compute SHA256 of PDF bytes for content-addressed storage. Triggered by any code touching .pdf files, page rasterization, or the triage stage.
---

# PDF extraction patterns

Project-specific patterns for `pymupdf` use in Pindorama. The library is also imported as `fitz`.

## Idiomatic open / close

`pymupdf.Document` is a context manager. Always use `with` so file handles release:

```python
import pymupdf

with pymupdf.open(pdf_path) as doc:
    for page in doc:
        text = page.get_text("text")
        ...
```

Avoid `doc = pymupdf.open(...)` followed by manual `doc.close()` — leaks on exception.

## Native-vs-OCR triage

The triage stage reads only the first 5 pages and computes a quality score. Do **not** read the entire PDF for triage — for a 500-page book this wastes minutes.

```python
with pymupdf.open(pdf_path) as doc:
    sample_pages = doc[: min(5, len(doc))]
    text_blob = "\n".join(p.get_text("text") for p in sample_pages)
```

Score components (literal — do not adjust without an ADR):

1. Ratio of valid (non-control, non-replacement) chars to total chars.
2. Average chars per page (very low → likely scan or image PDF).
3. Portuguese wordlist hit rate (proxy for "this is real text in PT, not garbage").
4. OCR-artifact heuristics (e.g. excessive single-char lines, rampant `?`, broken ligatures).

Classification: `native` (use pymupdf), `needs_ocr` (route to `dots.ocr`), `unusable` (skip, log).

## Page rasterization for OCR

`dots.ocr` and `chandra-ocr` consume rendered images. Render at **200–300 DPI**:

```python
import pymupdf

zoom = 300 / 72  # 72 = native PDF DPI
matrix = pymupdf.Matrix(zoom, zoom)

with pymupdf.open(pdf_path) as doc:
    for page_num, page in enumerate(doc):
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        # Stream to disk under $SLURM_SCRATCH (per-job NVMe), NOT /scratch.
        # PNG is lossless; for VLM input prefer JPEG quality 90 to halve I/O.
        out = scratch_dir / f"{sha256}_p{page_num:04d}.jpg"
        pix.save(out)
        del pix  # free C-side memory immediately
```

## Memory pressure pitfalls

- Each `Pixmap` holds raw RGB at 300 DPI ≈ 25 MB for an A4 page. **Delete after save** (`del pix`) — Python's GC will not run mid-loop fast enough on a 500-page book.
- Open one document at a time per worker. Sharing `Document` objects across processes has caused crashes.
- Render to `$SLURM_SCRATCH` (per-job NVMe), not `/scratch/alpine`. Clean up the rendered images **after** OCR succeeds for that PDF, not after the whole job — checkpoint discipline.

## Content-addressed storage

PDFs are stored under `<scratch>/raw_pdfs/<sha256>.pdf`. Compute SHA256 streaming, not by reading the whole file into memory:

```python
import hashlib
from pathlib import Path

def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()
```

See `docs/adr/0002-content-addressed-storage.md` for the rationale.

## Native text extraction (Stage 4a)

```python
with pymupdf.open(pdf_path) as doc:
    pages = [page.get_text("text") for page in doc]
text = "\n\n".join(pages)
out_path = extracted_text_dir / f"{sha256}.txt"
out_path.write_text(text, encoding="utf-8")
```

`get_text("text")` is the default and gives a flat reading order. Don't use `"blocks"` or `"dict"` for the first pass — overkill and breaks downstream paragraph reflow heuristics.

## What this skill does NOT cover

- VLM-based OCR (Stage 4b/4c) — see the `vllm-ocr` skill.
- Dehyphenation / paragraph reflow / dedup — see `src/pindorama/postprocess.py` (TBD) and Stage 5 of the bootstrap brief.
