# PROGRESS.md — Pindorama stage manifest & handoff

> **Statuses:** `[ ]` not started · `[~]` in progress · `[x]` done & verified · `[!]` blocked.
> Read this file's **top section first** on every fresh session.

## Scope reminder

**This repo is dataset-only.** Scrape → triage → extract → dedup → package → push to HF + Zenodo. The benchmark, the Gemma 4 26B MoE finetune, and the from-scratch pretraining all live in **separate, not-yet-created** repositories. Anything in `PINDORAMA_BOOTSTRAP_PROMPT.md` that talks about downstream training is historical context only — do not act on it here.

## Next agent should start by…

Asking Daniel the **two hard blockers** below (Q1 + Q2). Neither answer can be safely guessed without burning cycles on incompatible versions or putting the cleaned corpus on a purge-eligible filesystem.

After Daniel answers, the next concrete action is: **implement Stage 1 — the catalog scraper at `src/pindorama/scrape_catalog.py`**, modeled around `src/pindorama/db.py:connect()` for state and following the rate-limit / etiquette rules in `CLAUDE.md` and `PINDORAMA_BOOTSTRAP_PROMPT.md` §2 ("Stage 1 — Catalog Scraping"). Pair the scraper with `tests/test_scrape_catalog.py` covering pagination edge cases (single page, empty page, malformed row), and keep the scraper idempotent against `metadata.sqlite`.

The scaffolding bootstrapped in this commit is purely structural: directory layout, agent guides, ADRs, SLURM templates, sensors, and an empty `pyproject.toml` (deps deliberately unpinned — see Q1 below). No pipeline behavior exists yet, and no cluster work has happened — Daniel is on his Mac.

## Open verification questions for Daniel

### Hard blockers (must answer before Stage 1+)

1. **Pinned dependency versions.** `pyproject.toml` ships with empty pin lists. **Please supply canonical versions** for:
   - `torch`, `transformers`, `vllm` (these tend to be tightly coupled — what works on Alpine A100s today?).
   - `pymupdf`, `datasketch`, `datasets`, `huggingface_hub`, `fasttext-langdetect` *or* `lingua-language-detector`.
   - Commit hashes for `rednote-hilab/dots.ocr` and `datalab-to/chandra` (model repos and any inference-side dependencies).
   If you'd rather we propose a set, say so and we will — but pinning without your input on the cluster's known-good combinations risks burning cycles on incompatible versions.
2. **Long-term storage home for the cleaned corpus.** `/scratch/alpine/$USER` is purge-eligible and is the wrong final home. **Should the cleaned corpus live in `/projects/$USER/pindorama` or in PetaLibrary** (`docs/curc/petalibrary/`) for the publication build? The decision affects both `paths.py` and the dataset card's "Reproducing" section.

### Soft decision (proposed default, not blocking)

3. **Tokenizer for `token_count` reporting in the dataset card.** The dataset card needs a single canonical tokenizer for the per-doc and total token counts; downstream consumers can re-tokenize against whatever they want. **Proposed default:** `TucanoBR/Tucano-2b4` (currently the strongest public Portuguese model). Push back if you'd rather use a different tokenizer (e.g. the one shipped with the Gemma 4 26B MoE checkpoint, once you've selected it for the *finetune repo*). If silent, we ship the default.

## Stage manifest

```
[ ] Stage 1: catalog scrape         (src/pindorama/scrape_catalog.py, slurm/scrape_or_download.sh)
[ ] Stage 2: PDF download           (src/pindorama/download_pdfs.py,  slurm/scrape_or_download.sh)
[ ] Stage 3: triage                 (src/pindorama/triage.py,         slurm/triage.sh)
[ ] Stage 4a: native extraction     (src/pindorama/extract_native.py)
[ ] Stage 4b: OCR (dots.ocr)        (src/pindorama/extract_ocr_dots.py,    slurm/ocr_dots.sh)
[ ] Stage 4c: OCR fallback (chandra)(src/pindorama/extract_ocr_chandra.py, slurm/ocr_chandra.sh)
[ ] Stage 5: post-processing & dedup(src/pindorama/postprocess.py,    slurm/postprocess.sh)
[ ] Stage 6: corpus analysis        (src/pindorama/analyze_corpus.py)
[ ] Stage 7: HF packaging           (src/pindorama/package_hf.py)
[ ] Stage 8: Zenodo DOI             (manual; metadata block to paste — see skill `huggingface-dataset`)
```

## Cross-cutting work (track here when surfaced)

```
[ ] Pin dep versions in pyproject.toml once Q1 is answered; remove uv.lock from .gitignore at that point.
[ ] Add a CI workflow that runs `bash scripts/check.sh` on PRs (low priority — local `--full` covers it for now).
[ ] Decide whether to add a `tests/test_paths.py` smoke that constructs PindoramaPaths in a tmpdir.
[ ] Author a `docs/runbooks/zenodo-doi-mint.md` once Stage 7 is close (Stage 8 is interactive).
```

## Known constraints baked into the scaffolding (do not silently change)

- Hooks block `rm -rf` against `/`, `/scratch`, `/projects`, `/home`, `$SCRATCH`.
- Hooks block `git push --force` to `main`/`master` and `git commit` when staged diff contains `\bhf_[A-Za-z0-9]{32,}\b`.
- File writes are restricted to the repo, `/scratch/alpine/$USER/pindorama`, and `/projects/$USER/pindorama`.
- Post-edit hook runs `bash scripts/check.sh --fast` (lint only, ~sub-second).
- A100 VRAM is **not** hardcoded; OCR scripts must query at runtime.
- 2 req/s rate cap on `dominiopublico.gov.br` (scrape and download).

## Changelog

- **2026-05-04** — Bootstrap commit. Repository scaffold created per `PINDORAMA_BOOTSTRAP_PROMPT.md` §4 with a scope correction (this repo is dataset-only; benchmark / finetune / from-scratch-pretrain live in separate, not-yet-created repos). Existing CURC documentation moved into `docs/curc/` (preserved verbatim, ~134 files). GitHub repo renamed `danielxmed/curc-docs` → `danielxmed/pindorama`. All sensors green (`bash scripts/check.sh --full`).
