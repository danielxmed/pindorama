---
name: vllm-ocr
description: Use when running rednote-hilab/dots.ocr (primary) or datalab-to/chandra (fallback) inference at scale on Alpine GPUs via vLLM. Covers vLLM startup, batch sizing that queries VRAM at runtime, per-page confidence heuristics that drive the dots->chandra fallback queue, and checkpoint discipline so a SLURM kill loses ≤1 PDF.
---

# vLLM OCR patterns

Pindorama uses two VLM-based OCRs in series:

- **Primary:** `rednote-hilab/dots.ocr` (≈3B, Qwen2.5-VL-based) — fast, used on every `needs_ocr` PDF.
- **Fallback:** `datalab-to/chandra` (≈8B, Qwen3-VL-based) — slower, used **only** for pages flagged by the primary's per-page confidence heuristic.

This is a tiered strategy. See `docs/adr/0004-ocr-tiering-dots-then-chandra.md` for why.

## Confirm versions before coding

The bootstrap brief lists model names but **not pinned commit hashes or vLLM/transformers versions**. Before writing inference code, ask Daniel for canonical versions (PROGRESS.md verification question 3) and pin them in `pyproject.toml`. Do not invent version numbers.

## Query VRAM at runtime

Alpine `aa100` nodes have NVIDIA A100 GPUs that may be 40 GB or 80 GB. Never hardcode either — compute the budget from `nvidia-smi`:

```python
import subprocess

def gpu_total_mib() -> int:
    out = subprocess.check_output(
        ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
        text=True,
    )
    # Multi-GPU: take the minimum to be safe.
    return min(int(x.strip()) for x in out.splitlines() if x.strip())
```

vLLM's `gpu_memory_utilization` is a fraction in `[0, 1]`. A reasonable starting point on a single-tenant GPU is `0.85`. Tune downward if you hit OOM during a long shard.

## Batch sizing strategy

Start with **batch size 32** for `dots.ocr`. Adjust based on:

- VRAM (`gpu_total_mib()` above).
- Page resolution (200 vs 300 DPI changes pixel count, not just file size).
- Model size — chandra (8B) takes roughly the batch_size of dots.ocr (3B) divided by ≈3.

Don't tune batch size by hand on every job. Write a small calibration helper that does a binary search for the largest batch that fits in `< 0.85 * gpu_total_mib()`, called once per job at startup.

## vLLM startup pattern

```python
from vllm import LLM, SamplingParams

llm = LLM(
    model="rednote-hilab/dots.ocr",
    revision="<pinned-commit>",      # Ask Daniel; pin once known.
    trust_remote_code=True,
    gpu_memory_utilization=0.85,
    max_model_len=<from-model-card>,
    dtype="auto",
)

# OCR is deterministic transcription, not creative — keep temperature at 0.
sampling = SamplingParams(temperature=0.0, max_tokens=4096, stop=None)
```

Pass page images via the multimodal API. The exact field shape depends on the vLLM version — read `docs/curc/software/python.md` and the model card before the first run; do not paste an example from training data without verifying.

## Per-page confidence heuristic

For each rendered page → primary-OCR transcription, compute a confidence proxy:

- Output length vs page area (very short → likely OCR failure / blank → flag).
- Ratio of non-letter chars (heuristic for garbled output).
- Presence of repeated single-char "lines" (classic VLM stutter).
- Lang-ID (`fasttext-langdetect` or `lingua-py`) confidence on the page text being Portuguese.

If any threshold is breached, append the page to the chandra-fallback queue keyed by `(sha256, page_num)`. Replace only those pages in the final text file.

## Checkpoint discipline

A 500-page book takes minutes. A SLURM kill at hour 23:59 must not lose hours of work. Discipline:

- Write per-page transcription to a per-doc temp file under `$SLURM_SCRATCH` immediately after each batch returns.
- After all pages for a doc complete, atomically `os.replace(...)` the temp file into `<scratch>/extracted_text/<sha256>.txt`.
- Update the SQLite DB only **after** the atomic rename succeeds (`extraction_status='done'`, `extraction_method='dots_ocr'`).
- A killed mid-doc job restarts that doc from page 0; a killed between-docs job resumes at the next doc. Idempotency is checked via SQLite, not via file existence (a partial file is invalid until DB-confirmed).

## When this skill is NOT what you want

- SLURM headers / submission patterns → `alpine-slurm` skill.
- Page rasterization (DPI, Pixmap, save) → `pdf-extraction` skill.
- Post-OCR cleaning (dehyphenation, dedup) → Stage 5 in the bootstrap brief; not yet a skill.
