# Pindorama — Agent Anchor

Pindorama is a curated open dataset of Brazilian Portuguese literary public-domain texts, sourced from `dominiopublico.gov.br`, packaged for HuggingFace Hub + Zenodo. Target ≈220M tokens. See `README.md`.

**Scope: dataset-only.** Scrape → triage → extract → dedup → package → push. No model training, no finetune, no benchmark — those live in other (not-yet-created) repos.

This file is the feedforward anchor. Read it on every fresh session before touching anything else.

## Stack

- Python 3.11+, managed by `uv` (see `docs/curc/software/uv.md`).
- SQLite (single file, source of truth — see `docs/adr/0003-sqlite-as-source-of-truth.md`).
- `pymupdf` for native PDF + page rasterization.
- `vLLM` + `transformers` for `dots.ocr` (primary) and `chandra-ocr` (fallback).
- `datasketch` (MinHash/LSH dedup), `datasets` (HF packaging), `fasttext` / `lingua-py` (lang ID).

## Commands

- `uv sync` — install / lock deps.
- `uv run pytest` — unit tests.
- `bash scripts/check.sh` — aggregator sensor (lint + typecheck + tests). Run before every commit.
- SLURM submission: `module load slurm/alpine && sbatch slurm/<stage>.sh`. Templates in `slurm/`; never edit the headers' partition/QoS lines without reading `docs/curc/running-jobs/`.

## Cluster cheatsheet (CURC Alpine)

- Login: `ssh dame9177@login.rc.colorado.edu` (Duo MFA).
- Account: `ucb-general` (Trailhead Auto-Allocation — no special approval).
- Partitions: `amilan` (CPU default) · `aa100` (3× A100/node, 21-GPU cap across user's jobs) · `atesting_a100` (1× A100 MIG, ≤1h, debug).
- QoS: `normal` (≤24h) · `long` (≤7d) · `testing` (testing partitions only).
- Module system: LMOD. Always `module load slurm/alpine` before SLURM commands.

## Storage rule

- Code → this repo (committed).
- Persistent dataset / cleaned corpus → `/projects/$USER/pindorama` (NOT scratch).
- Hot working artifacts (raw PDFs, extracted text, intermediate state) → `/scratch/alpine/$USER/pindorama` (purge-eligible).
- Per-job ephemeral → `$SLURM_SCRATCH` (wiped at job end).
- `metadata.sqlite` is the source of truth. Daily backup to `<scratch>/backups/metadata_<date>.sqlite`.

## Hard "do not"

- Do **not** run pipeline workloads on login nodes. Submit them via `sbatch`.
- Do **not** commit `HF_TOKEN`, any `.env*` (except `.env.example`), or files matching `\bhf_[A-Za-z0-9]{32,}\b`.
- Do **not** push to `main` without a PR. Force-push to `main` is hook-blocked.
- Do **not** delete files in `/scratch/alpine/$USER` without first backing up `metadata.sqlite`.
- Do **not** encode A100 VRAM size or GPU count as constants — query at runtime (`nvidia-smi --query-gpu=memory.total`). 21 is the simultaneous-job cap, not an allocation.
- Do **not** exceed 2 req/s against `dominiopublico.gov.br` (scrape or download).
- Do **not** preemptively add scaffolding for failures you have not observed.

## Pointers

- `docs/curc/` — authoritative CURC documentation (clusters, slurm, software, ondemand).
- `docs/adr/` — design decisions; read before changing storage/pipeline shape.
- `docs/conventions.md` — Python style, logging schema, naming.
- `docs/runbooks/` — connect-to-Alpine, recover-from-failed-stage.
- `.claude/skills/` — load on demand: `alpine-slurm`, `pdf-extraction`, `vllm-ocr`, `huggingface-dataset`.
- `PROGRESS.md` — stage manifest + handoff note. Read top first on every session resume.

## Operating principles (brief)

- Minimum harness. Add scaffolding only in response to observed failure.
- Idempotency at every stage: check DB before doing work; resume safely after SLURM kill.
- Sensors over guides where possible. Linter over CLAUDE.md exhortation.
- Ask Daniel before guessing on the four open questions in `PROGRESS.md`.
