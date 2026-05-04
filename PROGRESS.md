# PROGRESS.md — Pindorama stage manifest & handoff

> **Statuses:** `[ ]` not started · `[~]` in progress · `[x]` done & verified · `[!]` blocked.
> Read this file's **top section first** on every fresh session.

## Scope reminder

**This repo is dataset-only.** Scrape → triage → extract → dedup → package → push to HF + Zenodo. The benchmark, the Gemma 4 26B MoE finetune, and the from-scratch pretraining all live in **separate, not-yet-created** repositories.

## Next agent should start by…

**Implement Stage 1 — the catalog scraper at `src/pindorama/scrape_catalog.py`**, modeled around `src/pindorama/db.py:connect()` for state and following the rate-limit / etiquette rules in `CLAUDE.md` (≤2 req/s against `dominiopublico.gov.br`). Pair the scraper with `tests/test_scrape_catalog.py` covering pagination edge cases (single page, empty page, malformed row), and keep the scraper idempotent against `metadata.sqlite`.

`pyproject.toml` is now pinned (2026-05-04) and `uv.lock` is tracked. `bash scripts/check.sh --full` is green on a compute node. Use `uv sync --dev` for local testing; SLURM scripts that import the OCR stack must use `uv sync --extra ocr`.

## Open verification questions for Daniel

### Resolved (2026-05-04)

1. ~~**Pinned dependency versions.**~~ **Resolved.** Daniel delegated the research; `pyproject.toml` is now pinned and `uv.lock` is committed. Base set: httpx, beautifulsoup4, lxml, pymupdf, datasketch, lingua-language-detector, datasets, huggingface-hub. OCR extra (`--extra ocr`): vllm 0.20, torch 2.11.0 (forced by vllm), transformers 5.6+. Python floor bumped to 3.12 (lingua-language-detector 2.x requirement). Validated on compute node 2026-05-04: `bash scripts/check.sh --full` green.
2. ~~**Long-term storage home for the cleaned corpus.**~~ **Answered: `/projects/$USER/pindorama`.** Already encoded in `src/pindorama/paths.py:default()` (was the bootstrap default; now confirmed). PetaLibrary is **not** in scope.
3. ~~**Tokenizer for `token_count` reporting.**~~ **Default accepted by silence: `TucanoBR/Tucano-2b4`.** The dataset card will report token counts against that tokenizer when Stage 7 ships. Re-open this question only if Daniel explicitly asks for a different tokenizer in a later session.

### Currently open

(none)

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
[x] Pin dep versions in pyproject.toml + commit uv.lock (2026-05-04, validated on atesting partition).
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

- **2026-05-04** — Bootstrap commit. Repository scaffold created with a dataset-only scope (benchmark / finetune / from-scratch-pretrain live in separate, not-yet-created repos). Existing CURC documentation moved into `docs/curc/` (preserved verbatim, ~134 files). GitHub repo renamed `danielxmed/curc-docs` → `danielxmed/pindorama`. All sensors green (`bash scripts/check.sh --full`).
- **2026-05-04** — Verification questions resolved. Q2 (storage home): Daniel confirmed `/projects/$USER/pindorama` — already the default in `paths.default()`, no code change. Q1 (deps): deferred — Daniel will research and pin `pyproject.toml` manually; agent must wait. Q3 (tokenizer): default `TucanoBR/Tucano-2b4` accepted by silence.
- **2026-05-04** — Removed `PINDORAMA_BOOTSTRAP_PROMPT.md` (the bootstrap-time scaffolding prompt). Inlined the few load-bearing references; cluster repo path moved from `/projects/$USER/curc-docs` → `/projects/$USER/pindorama/repo`.
- **2026-05-04** — Pinned `pyproject.toml` (Daniel delegated research). Python floor 3.11→3.12. Committed `uv.lock`. Validated on `atesting` partition: 207 packages resolved, 58 dev packages installed in 5s, `bash scripts/check.sh --full` green.
