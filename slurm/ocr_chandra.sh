#!/bin/bash
#SBATCH --job-name=pindorama-ocr-chandra
#SBATCH --account=ucb-general
#SBATCH --partition=aa100
#SBATCH --qos=long
#SBATCH --time=72:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=10
#SBATCH --gres=gpu:1
#SBATCH --mem=64G
#SBATCH --output=/scratch/alpine/%u/pindorama/logs/ocr_chandra-%j.out
#SBATCH --error=/scratch/alpine/%u/pindorama/logs/ocr_chandra-%j.err
#
# Stage 4b — OCR scanned PDFs with datalab-to/chandra-ocr-2 (HuggingFace path).
#
# For every row with triage_status='needs_ocr' AND extraction_status IS NULL,
# render pages with pymupdf (200 DPI, the chandra-recommended sweet spot),
# batch them through chandra-ocr-2 (8B params, bf16 ~17 GiB on a single A100)
# via chandra's `InferenceManager(method="hf")`, and write
# <scratch>/extracted_text/<sha>.txt with extraction_status='success' /
# extraction_method='ocr_chandra'. token_count stays NULL — Stage 6 backfills.
#
# Volume: 75 docs / 16,224 pages total (audited 2026-05-05, avg 216, max 778
# pages/doc). Smoke on a full A100 (40 GB) measured ~43 s/page in HF mode —
# 8B params at bf16 with no flash-attn fast-path. Best-case full-corpus wall
# ≈ 50–70 hours; we use `long` QoS (single slot, ≤7d) and a 72h budget so a
# single job can plausibly cover it. Resubmits are safe (idempotent on
# extraction_status IS NULL); a kill mid-doc loses that doc's partial work
# (the next run re-OCRs it from page 0), but no other doc's progress.
#
# Single-job (no array) — long QoS doesn't allow more than one anyway, and
# fanning across multiple GPUs from the same DB needs partitioning logic we
# don't have (Stage 4b doesn't shard).
#
# A100 VRAM is queried at runtime (the cluster has both 40 GB and 80 GB
# nodes); never hardcode. page_batch_size is bumped to 8 from the dev
# default of 4 since prod runs on a full A100, where the bigger batch is
# the easiest 1.5–2× speedup over the smoke profile.
#
# See ADR-0005 for why chandra-ocr-2 replaced the originally-planned
# vllm + dots.ocr stack.
set -euo pipefail

# slurm/alpine is for the login-node submit shell; inside a SLURM job the
# environment is already configured. Just need uv.
module load uv

PROJECT_ROOT="/projects/${USER}/pindorama/repo"
SCRATCH_ROOT="/scratch/alpine/${USER}/pindorama"

mkdir -p "${SCRATCH_ROOT}/logs" "${SCRATCH_ROOT}/extracted_text"

# HF model cache lives on per-job NVMe — /home is capped at 2 GiB and the
# chandra-ocr-2 weights alone are ~17 GiB. Re-downloading per job is the
# trade-off we accept: $SLURM_SCRATCH is wiped at job end, so caching across
# jobs would need /scratch/alpine. Acceptable for a one-off Stage 4b run.
mkdir -p "${SLURM_SCRATCH}/hf-cache"
export HF_HOME="${SLURM_SCRATCH}/hf-cache"

cd "${PROJECT_ROOT}"

# Diagnostic — confirms which A100 SKU and driver we landed on.
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv 1>&2

uv sync --extra ocr --frozen

uv run --no-sync python -m pindorama.extract_ocr_chandra \
  --db "${SCRATCH_ROOT}/metadata.sqlite" \
  --extract-dir "${SCRATCH_ROOT}/extracted_text" \
  --page-batch-size 8
