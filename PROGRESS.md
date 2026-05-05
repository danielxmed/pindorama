# PROGRESS.md — Pindorama stage manifest & handoff

> **Statuses:** `[ ]` not started · `[~]` in progress · `[x]` done & verified · `[!]` blocked.
> Read this file's **top section first** on every fresh session.

## Scope reminder

**This repo is dataset-only.** Scrape → triage → extract → dedup → package → push to HF + Zenodo. The benchmark, the Gemma 4 26B MoE finetune, and the from-scratch pretraining all live in **separate, not-yet-created** repositories.

## Next agent should start by…

**Awaiting Daniel's review/merge of the Stage 1 change** (working tree only — `src/pindorama/scrape_catalog.py` ~340 lines + 31 tests in `tests/test_scrape_catalog.py` + fixtures under `tests/fixtures/dominiopublico/`; `pyproject.toml` adds `playwright>=1.59,<2`; `slurm/scrape_or_download.sh` rewired into a real launcher; `uv.lock` regenerated). Validation already green on compute node (SLURM job 26677688, atesting partition, `bash scripts/check.sh --full`).

**After merge, implement Stage 2 — `src/pindorama/download_pdfs.py`.** Concretely: the catalog list does NOT expose `pdf_url`, so Stage 1 leaves that column NULL. Stage 2 must fetch each detail page (canonical `catalog_url` shape: `https://dominiopublico.gov.br/pesquisa/DetalheObraForm.do?co_obra=<id>`) and parse out the `Baixar` button URL `/pesquisa/DetalheObraDownload.do?co_obra=<id>&co_midia=2`. The detail page only populates correctly when the request includes `select_action=` (empty) AND a Referer header (observed via Chrome MCP during fixture capture). Whole domain is gated by Cloudflare managed challenge → Stage 2 must also use Playwright headless Chromium, not plain httpx. Model on `scrape_catalog.py` for browser lifecycle + rate-limit / SQLite state-machine patterns.

`pyproject.toml` is pinned and `uv.lock` is tracked. Use `uv sync --dev` for local testing; SLURM scripts that import the OCR stack must use `uv sync --extra ocr`.

## Open verification questions for Daniel

### Resolved (2026-05-04)

1. ~~**Pinned dependency versions.**~~ **Resolved.** Daniel delegated the research; `pyproject.toml` is now pinned and `uv.lock` is committed. Base set: httpx, beautifulsoup4, lxml, pymupdf, datasketch, lingua-language-detector, datasets, huggingface-hub. OCR extra (`--extra ocr`): vllm 0.20, torch 2.11.0 (forced by vllm), transformers 5.6+. Python floor bumped to 3.12 (lingua-language-detector 2.x requirement). Validated on compute node 2026-05-04: `bash scripts/check.sh --full` green.
2. ~~**Long-term storage home for the cleaned corpus.**~~ **Answered: `/projects/$USER/pindorama`.** Already encoded in `src/pindorama/paths.py:default()` (was the bootstrap default; now confirmed). PetaLibrary is **not** in scope.
3. ~~**Tokenizer for `token_count` reporting.**~~ **Default accepted by silence: `TucanoBR/Tucano-2b4`.** The dataset card will report token counts against that tokenizer when Stage 7 ships. Re-open this question only if Daniel explicitly asks for a different tokenizer in a later session.

### Currently open

1. **Live Playwright Chromium launch on compute nodes — does it actually work end-to-end?** The Stage 1 test suite is green (mocked Playwright), but the live scraper has not yet been driven against `dominiopublico.gov.br` from a compute node. The launcher in `slurm/scrape_or_download.sh` runs `playwright install chromium --no-deps` (cache pinned to `/projects/$USER/pindorama/playwright-browsers/`). If Chromium fails to launch for missing system libs at runtime, fallback runbook is to switch to apptainer per `docs/curc/software/apptainer.md`. Verify on first live run.

## Stage manifest

```
[~] Stage 1: catalog scrape         (src/pindorama/scrape_catalog.py, slurm/scrape_or_download.sh) — implemented, awaiting Daniel's review/merge
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
- `dominiopublico.gov.br` is fronted by Cloudflare managed challenge (`cf-mitigated: challenge` on plain GETs). All fetches against the domain must go through Playwright headless Chromium — plain httpx returns 403. Chromium cache lives at `/projects/$USER/pindorama/playwright-browsers/` (NOT `~/.cache/`, which would blow the 2 GiB `/home` quota).

## Changelog

- **2026-05-04** — Bootstrap commit. Repository scaffold created with a dataset-only scope (benchmark / finetune / from-scratch-pretrain live in separate, not-yet-created repos). Existing CURC documentation moved into `docs/curc/` (preserved verbatim, ~134 files). GitHub repo renamed `danielxmed/curc-docs` → `danielxmed/pindorama`. All sensors green (`bash scripts/check.sh --full`).
- **2026-05-04** — Verification questions resolved. Q2 (storage home): Daniel confirmed `/projects/$USER/pindorama` — already the default in `paths.default()`, no code change. Q1 (deps): deferred — Daniel will research and pin `pyproject.toml` manually; agent must wait. Q3 (tokenizer): default `TucanoBR/Tucano-2b4` accepted by silence.
- **2026-05-04** — Removed `PINDORAMA_BOOTSTRAP_PROMPT.md` (the bootstrap-time scaffolding prompt). Inlined the few load-bearing references; cluster repo path moved from `/projects/$USER/curc-docs` → `/projects/$USER/pindorama/repo`.
- **2026-05-04** — Pinned `pyproject.toml` (Daniel delegated research). Python floor 3.11→3.12. Committed `uv.lock`. Validated on `atesting` partition: 207 packages resolved, 58 dev packages installed in 5s, `bash scripts/check.sh --full` green.
- **2026-05-04** — Stage 1 (catalog scraper) implemented end-to-end; sensors green via SLURM job 26677688; awaiting Daniel's review/merge.
