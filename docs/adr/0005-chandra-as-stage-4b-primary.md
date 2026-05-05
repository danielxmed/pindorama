# ADR-0005 — Stage 4b OCR primary: chandra-ocr-2 (supersedes ADR-0004)

**Status:** Accepted
**Date:** 2026-05-05

## Context

ADR-0004 specified a two-stage OCR design: `rednote-hilab/dots.ocr` (3B,
fast) over every `needs_ocr` PDF, with a per-page confidence proxy routing
hard pages to `datalab-to/chandra` (8B, slower, more accurate) as a Stage 4c
fallback. The plan also pinned `vllm>=0.20,<0.21` in the `ocr` extra of
`pyproject.toml` to serve dots.ocr efficiently.

When implementing Stage 4b on CURC Alpine, the vLLM install path failed:

- vllm 0.20.1 publishes only `manylinux_2_35_x86_64` wheels (PyPI listing).
- CURC Alpine compute nodes run RHEL 8.10 with **glibc 2.28**. The
  `manylinux_2_35` tag requires glibc ≥ 2.35, so the wheel is rejected and
  uv falls back to building from source.
- The source build needs CUDA 13 to compile vllm's CUDA kernels. The cluster
  software tree provides CUDA 11.x and 12.x (12.9 via `nvhpc_sdk/2025.255`)
  but no 12.x toolkit could complete the cmake link step.
- vLLM upstream documents the requirement explicitly: *"vLLM requires glibc
  ≥ 2.35 (Ubuntu 22.04+, Debian 12+, RHEL 9+)"*.

A workable container path exists (`apptainer pull docker://vllm/vllm-openai`)
but adds a non-trivial layer of indirection between Python orchestration
code and the model server. For a single-stage 75-doc OCR pass, the cost
of that layer outweighs the throughput edge of vLLM batching.

Independently, the new `chandra-ocr-2` release (datalab, 2026-03) shipped
with strong PT-BR multilingual benchmark numbers — 95.2% on the 43-language
multilingual eval, top among OSS OCR models the team has surveyed — and a
clean HuggingFace inference path with no glibc/CUDA wheel dependencies
(pure-Python wheel + torch ≥ 2.8 with manylinux_2_28 wheels). PT-BR was a
key selection criterion for a Brazilian-Portuguese literary corpus.

## Decision

Replace the dots.ocr-then-chandra tiering with **chandra-ocr-2 as the
unconditional Stage 4b primary**, executed via chandra's
`InferenceManager(method="hf")`. The Stage 4c fallback is removed from the
active pipeline plan.

Concrete changes:

- `pyproject.toml` `ocr` extra: drop `vllm`, add `chandra-ocr[hf]>=0.2,<0.3`;
  keep `torch==2.11.0` and `transformers>=5.6,<6` as constraints.
- `src/pindorama/extract_ocr_chandra.py` is the Stage 4b entry point;
  `slurm/ocr_chandra.sh` is the SLURM submit script (single job on `aa100`).
- DB column `extraction_method` is `'ocr_chandra'` for these rows.
- Stage 4c is marked `[~] DEFERRED` in PROGRESS.md. If a real-world quality
  audit on the 75-doc set surfaces systematic failures, we'll re-open with a
  concrete target — likely a different model entirely, since chandra cannot
  fall back to itself, and dots.ocr would need the same container workaround
  that motivated this ADR in the first place.

## Consequences

- **Pro:** No glibc/CUDA wheel hell. chandra-ocr is a pure-Python package;
  torch 2.11 has manylinux_2_28 wheels (post-PyTorch 2.6 platform switch);
  install path is `uv sync --extra ocr --frozen` with no toolkit modules to
  load.
- **Pro:** Best-in-class PT-BR accuracy (95.2% multilingual bench).
  dots.ocr 1.5 was at ~85% PT.
- **Pro:** Single-stage simplifies the pipeline. No confidence-proxy
  threshold tuning, no per-page routing, no two model loads per node.
- **Pro:** chandra's parser already strips page-headers/footers (running
  titles + page numbers) when consuming `prompt_type="ocr_layout"`, which
  removes the Stage 5 cleanup load.
- **Con:** Slower per-page than dots.ocr would have been (chandra-ocr-2 is
  8B vs 3B). For 75 docs this is a non-issue at our wall budget; would
  matter at hundreds of thousands of pages.
- **Con:** No automatic fallback. A doc that chandra renders poorly stays
  poorly rendered until we manually intervene. Triage already filters out
  the 1989 native-extractable docs, so the OCR set is small enough to spot-
  check by eye if needed.
- **Con:** chandra weights (~17 GiB bf16) re-download per SLURM job because
  HF cache lives in `$SLURM_SCRATCH`. Acceptable for a one-shot Stage 4b;
  worth caching to `/scratch/alpine` if we re-run.

## Alternatives considered

- **Container-served vLLM + dots.ocr** (`apptainer pull docker://vllm/...`).
  Bypasses glibc/CUDA mismatch but requires gluing together a long-running
  vLLM server and Python orchestration — extra moving parts for a one-off
  75-doc workload.
- **Downgrade vLLM to a manylinux_2_28-compatible release** (e.g. 0.10.x or
  0.11.x). Would pin us to the version that first added `DotsOCRForCausalLM`
  support without all the 0.20-era polish. Also still ties us to the dual-
  model tiering plan that we're walking away from for accuracy reasons.
- **DeepSeek-OCR-2 via transformers.** Strong contender but its README pins
  `transformers==4.46.3` and `torch==2.6.0`, both well below our floor; also
  needs `flash-attn==2.7.3` which is its own CUDA build dance. Ruled out as
  more deps churn for a smaller PT-BR accuracy win than chandra.
- **GLM-OCR (`zai-org/GLM-OCR`).** Requires `transformers` HEAD (`pip install
  git+https://...`); no PT-BR bench; Chinese-first. Ruled out on PT-BR
  uncertainty and dep instability.

## References

- [vLLM Installation requirements](https://docs.vllm.ai/en/latest/getting_started/installation/) — glibc ≥ 2.35.
- [vLLM issue #22798](https://github.com/vllm-project/vllm/issues/22798) — glibc 2.28 incompatibility.
- [chandra-ocr GitHub](https://github.com/datalab-to/chandra) — package code, `InferenceManager`.
- [datalab-to/chandra-ocr-2 on HF](https://huggingface.co/datalab-to/chandra-ocr-2) — model card + benchmarks.
