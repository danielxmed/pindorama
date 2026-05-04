# ADR-0004 — OCR tiering: dots.ocr first, chandra-ocr fallback

**Status:** Accepted
**Date:** 2026-05-04

## Context

A non-trivial fraction of `dominiopublico.gov.br` PDFs are scans of older books — the native `pymupdf.get_text()` path returns garbage for them, so they go to a VLM-based OCR. Two candidate OCR systems are in scope:

- **`rednote-hilab/dots.ocr`** — ≈3B parameters, Qwen2.5-VL-based, fast, generally clean transcription on standard layouts.
- **`datalab-to/chandra`** — ≈8B parameters, Qwen3-VL-based, slower but more robust on hard pages: complex multi-column layouts, marginalia, faded scans, mixed scripts.

If we always use chandra, the GPU bill (`aa100` partition, 21-GPU cap) blows up: per-page latency is ≈3× and the corpus is large enough that the difference is days vs weeks of wallclock.

If we always use dots.ocr, we accept a quality floor on hard pages — and the publication artifact ships with avoidable noise.

## Decision

Use **dots.ocr as the primary OCR for every needs_ocr PDF**. After each page transcription, compute a per-page confidence proxy:

- Output length vs page area (very short → blank or failure).
- Ratio of non-letter characters to letters (proxy for garbled output).
- Repeated single-character "lines" (classic VLM stutter).
- Language-ID confidence of the page text being Portuguese.

Pages whose proxy breaches the threshold are appended to a **chandra fallback queue** keyed by `(sha256, page_num)`. After dots.ocr finishes, run chandra over only the queued pages and **replace** those pages in the corresponding output file.

The DB records `extraction_method` per document. If any page in a doc was rerun under chandra, the column is `chandra_ocr` (the fallback wins for accounting purposes); a future column `extraction_method_per_page` may be added if granular reporting is required.

## Consequences

- **Pro:** Fast common case, accurate hard case. Estimated 80–90% of pages stay on dots.ocr.
- **Pro:** The fallback queue is a natural retry / requeue surface: tune the confidence threshold without re-running dots.ocr on the whole corpus.
- **Pro:** Failure isolation: chandra OOMs or errors do not lose the dots.ocr output already on disk.
- **Con:** Two model loads per node. Mitigated by running chandra in a separate `slurm/ocr_chandra.sh` array job after dots.ocr completes — never two VLMs in one process.
- **Con:** The confidence proxy is heuristic and will sometimes route easy pages to chandra and miss garbled ones. Tracked: tune the threshold once Stage 4 ships and we have data.

## Alternatives considered

- **Always chandra.** Rejected on cost.
- **Always dots.ocr.** Rejected on quality floor.
- **Page-classifier model upstream.** Rejected as premature: the confidence proxy is essentially a poor man's classifier and is good enough until we observe failure modes that require a real one.
- **Ensemble (run both, pick best).** Rejected on cost — doubles GPU spend for marginal gain on the easy majority of pages.
