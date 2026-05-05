# PROGRESS.md — Pindorama stage manifest & handoff

> **Statuses:** `[ ]` not started · `[~]` in progress · `[x]` done & verified · `[!]` blocked.
> Read this file's **top section first** on every fresh session.

## Scope reminder

**This repo is dataset-only.** Scrape → triage → extract → dedup → package → push to HF + Zenodo. The benchmark, the Gemma 4 26B MoE finetune, and the from-scratch pretraining all live in **separate, not-yet-created** repositories.

## Next agent should start by…

**Stages 1, 2, 3, 4a green; Stage 4b code green and prod RUNNING (job 26731346, expected ~50–70 h).** 2064 PDFs harvested; triage routed 1989 → native (Stage 4a, all green) and 75 → OCR. **Stage 4b uses `datalab-to/chandra-ocr-2` via the HuggingFace path** (not vllm/dots.ocr — see ADR-0005 for the pivot rationale: vllm wheels need glibc ≥ 2.35 and torch's PyPI default cu13 wheel needs CUDA driver ≥ 13.0; CURC Alpine is RHEL 8.10 / glibc 2.28 / driver 570.124.06 → CUDA 12.8). The pyproject points `torch`/`torchvision` at PyTorch's `cu128` index to land a CUDA 12-compatible wheel. Stage 4c (originally chandra fallback) is `DEFERRED` — single-stage chandra primary makes it redundant unless a post-run quality audit surfaces systematic issues. **When prod completes, next is Stage 5 (post-processing & dedup)**: read every `<sha>.txt` from `extracted_text/`, run MinHash/LSH dedup with `datasketch`, language-ID with `lingua`, write cleaned outputs to `<scratch>/cleaned_text/`. Stage 5 is the first stage that may collapse rows (the 7 SHA-shared catalog entries already point at the same `.txt`).

`pyproject.toml` is pinned and `uv.lock` is tracked. Use `uv sync --dev` for local testing; OCR jobs use `uv sync --extra ocr` (which now pulls `chandra-ocr[hf]` — torch 2.11, transformers 5.6+, accelerate, no vllm).

## Open verification questions for Daniel

### Resolved (2026-05-04)

1. ~~**Pinned dependency versions.**~~ **Resolved.** Daniel delegated the research; `pyproject.toml` is now pinned and `uv.lock` is committed. Base set: httpx, beautifulsoup4, lxml, pymupdf, datasketch, lingua-language-detector, datasets, huggingface-hub. OCR extra (`--extra ocr`): vllm 0.20, torch 2.11.0 (forced by vllm), transformers 5.6+. Python floor bumped to 3.12 (lingua-language-detector 2.x requirement). Validated on compute node 2026-05-04: `bash scripts/check.sh --full` green.
2. ~~**Long-term storage home for the cleaned corpus.**~~ **Answered: `/projects/$USER/pindorama`.** Already encoded in `src/pindorama/paths.py:default()` (was the bootstrap default; now confirmed). PetaLibrary is **not** in scope.
3. ~~**Tokenizer for `token_count` reporting.**~~ **Default accepted by silence: `TucanoBR/Tucano-2b4`.** The dataset card will report token counts against that tokenizer when Stage 7 ships. Re-open this question only if Daniel explicitly asks for a different tokenizer in a later session.

### Currently open

1. ~~**Live Playwright Chromium launch on compute nodes — does it actually work end-to-end?**~~ **Resolved (2026-05-05) via sibling-clone harness.** Stock Playwright Chromium hits Cloudflare turnstile on CURC ASN (interstitial doesn't clear in 90s). Scrapling 0.4.7's `StealthySession` (patchright stealth Chromium with bundled fingerprints) does clear it — 2064 PDFs harvested 2026-05-05. The in-repo Stage 1 implementation is still on a branch with stock Playwright; if it ever runs against live, it'll likely block on Cloudflare unless Daniel either (a) switches to scrapling, or (b) accepts that the in-repo path is residential-IP-only. Pipeline doesn't need this resolved because the harvest is done.

## Stage manifest

```
[x]* Stage 1: catalog scrape        (src/pindorama/scrape_catalog.py, slurm/scrape_or_download.sh) — in-repo PR still on branch; functionally satisfied via sibling-clone harvest 2026-05-05
[x]* Stage 2: PDF download          (src/pindorama/download_pdfs.py,  slurm/scrape_or_download.sh) — completed via sibling-clone harness; 2064/2077 = 99.4% in metadata.sqlite
[x]  Stage 3: triage                 (src/pindorama/triage.py,         slurm/triage.sh) — done 2026-05-05 on amilan job 26724148; 1989 native, 75 needs_ocr, 0 errors
[x]  Stage 4a: native extraction     (src/pindorama/extract_native.py, slurm/extract_native.sh) — done 2026-05-05 on amilan job 26725426; 1989 success / 0 errors / 1982 distinct .txt files (7 SHA dups collapsed)
[~] Stage 4b: OCR (chandra-ocr-2)   (src/pindorama/extract_ocr_chandra.py, slurm/ocr_chandra.sh) — code merged-ready; prod job 26731346 RUNNING on aa100 (qos=long, 72h budget). See ADR-0005 for the pivot off vllm/dots.ocr.
[~] Stage 4c: OCR fallback           DEFERRED. Originally chandra-as-fallback; now chandra is the unconditional primary (Stage 4b). Re-open only if a quality audit surfaces systematic chandra failures.
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
- **2026-05-05** — Stages 1+2 unblocked via **sibling-clone harness** at `/projects/dame9177/scrapling/harness/` (NOT in this repo). The in-repo Playwright-based scraper hit Cloudflare turnstile on CURC ASN; the sibling clone uses Scrapling's `StealthySession` (Camoufox-style stealth Chromium via `patchright`) which clears the non-interactive turnstile fine. Final harvest: **2064 PDFs / 2077 catalog rows = 99.4%** (SLURM job 26720895 main + 26722256 retry; ~13 errors are mostly genuine 404s). Reconciled into `/scratch/alpine/$USER/pindorama/metadata.sqlite` via `reconcile_to_pindorama.py` (job 26722376, 3s). Stage 3+ pipeline reads `metadata.sqlite` exactly as ADR-0003 prescribes — no schema change needed. Two scrapling 0.4.7 bugs found and worked around in the harness (unbounded cloudflare solver loop + Playwright close-hang after alarm-killed fetch); see `/projects/dame9177/scrapling/harness/harvest_batch.py` docstring. The in-repo Stage 1 PR remains untouched and would still work on a residential IP.
- **2026-05-05** — **Stage 3 (triage) implemented and run end-to-end** (PR pending). `src/pindorama/triage.py` opens each PDF with pymupdf, samples up to the first 5 pages, and classifies into `{native_extractable, needs_ocr, corrupted, too_small}` based on a 200-char/page threshold. CPU-only, sequential (~30 s for 2064 docs at avg ~15 ms/doc on amilan). Validated on atesting (job 26723908: lint+typecheck+50 unit tests green, smoke 20 docs OK). Production run on amilan (job 26724148): **1989 native_extractable (96.4%) / 75 needs_ocr (3.6%) / 0 corrupted / 0 too_small / 0 error_log**. Benign `MuPDF error: format error: No default Layer config` lines hit stdout for some docs — they don't affect extraction (PDF optional-content / layer config quirk only); Stage 4a/b will see the same lines and ignore them.
- **2026-05-05** — **Stage 4a (native extraction) implemented and run end-to-end** (PR pending). `src/pindorama/extract_native.py` opens each `native_extractable` PDF with pymupdf, pulls per-page text via `page.get_text("text")`, joins with `\f` (form feed) so downstream consumers can resplit page-by-page, writes UTF-8 (no BOM) to `<scratch>/extracted_text/<sha>.txt`, and persists `extraction_status='success'` / `extraction_method='native'` / `extracted_path` / `token_count=NULL` (Stage 6 will backfill against `TucanoBR/Tucano-2b4`). Validated on atesting (job 26725415: lint+typecheck+73 unit tests green, 20-row dry-run OK; job 26725422: 5-row real-mode smoke green, sample text confirmed clean PT-BR). Production run on amilan (job 26725426): **1989 success / 0 errors** in 2:31 wall (~76 ms/doc avg), MaxRSS 84 MiB, single-process. On-disk: 1982 distinct `<sha>.txt` files because **7 file_sha256 values are shared by 2 catalog rows each** — both rows correctly point at the same content-addressed text file (natural dedup; Stage 5 will collapse the catalog rows). All `error_log` cleared, all `token_count` NULL by design.
- **2026-05-05** — **Stage 4b pivoted from vllm/dots.ocr to chandra-ocr-2** (ADR-0004 → ADR-0005). The original plan pinned `vllm>=0.20,<0.21` to serve `rednote-hilab/dots.ocr`; on first install attempt vllm 0.20.1 failed because its only Linux wheel is `manylinux_2_35_x86_64` (glibc ≥ 2.35) and CURC Alpine compute nodes run RHEL 8.10 / glibc 2.28. Source build needed CUDA 13; cluster max is CUDA 12.9 (`nvhpc_sdk/2025.255`), and even that linker step failed (missing `cudadevrt`/`cudart_static` in nvhpc's cuda subtree). Daniel chose `datalab-to/chandra-ocr-2` over container-served vllm: pure-Python wheel, top PT-BR multilingual benchmark (95.2%), single-stage so Stage 4c becomes redundant. `pyproject.toml` `ocr` extra now lists `chandra-ocr[hf]>=0.2,<0.3` instead of vllm; uv.lock regenerated (vllm + ~60 transitive deps removed; chandra + accelerate + pypdfium2 + pydantic-settings added). `extraction_method='ocr_chandra'`. The `slurm/ocr_dots.sh` stub was removed; `slurm/ocr_chandra.sh` is the new Stage 4b submit script. After deps swap, a second blocker hit: `torch==2.11.0` from PyPI defaults to a `+cu130` wheel that requires NVIDIA driver ≥ 13.0; cluster has 570.124.06 (CUDA 12.8 max) → `cuda.is_available()` returns False with a "driver too old" warning. Fix in `pyproject.toml`: added a `[[tool.uv.index]]` for `https://download.pytorch.org/whl/cu128` and pointed `torch`+`torchvision` at it via `[tool.uv.sources]`; uv.lock now resolves to `torch==2.11.0+cu128`. Validation green via `validate_extract_ocr_chandra.sh` on atesting_a100 (lint+typecheck+102 unit tests+secret scan+dry-run, 14 s wall on the cu128 lock). Smoke (job 26729749) confirmed end-to-end load + per-page inference at ~43 s/page on a 40 GB A100 with no flash-attn fast path; cancelled after that throughput data was in hand. Production submitted as job 26731346 on `aa100` with `qos=long`, 72 h time budget, `--page-batch-size 8` for the bigger-A100 throughput edge — 75 docs / ~16,224 pages / expected wall 50–70 h. PR pending until prod completes so its stats land in the description.
