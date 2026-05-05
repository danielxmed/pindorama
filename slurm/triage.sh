#!/bin/bash
#SBATCH --job-name=pindorama-triage
#SBATCH --account=ucb-general
#SBATCH --partition=amilan
#SBATCH --qos=normal
#SBATCH --time=01:30:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=8G
#SBATCH --output=/scratch/alpine/%u/pindorama/logs/triage-%j.out
#SBATCH --error=/scratch/alpine/%u/pindorama/logs/triage-%j.err
#
# Stage 3 — triage. CPU-only, sequential.
#
# For every row with download_status='success' AND triage_status IS NULL,
# open the PDF with pymupdf, sample the first 5 pages, and classify into
# {native_extractable, needs_ocr, corrupted, too_small}. The result is
# written back to works.triage_status and (on failure) works.error_log.
#
# Volume is ~2k PDFs at ~100 ms each → ~3 min sequential, well under the
# 1h30 wall budget. If volume grows or per-doc cost rises, parallelize via
# multiprocessing.Pool with a single DB-writer process.
#
# Resubmits are safe: idempotency comes from the `triage_status IS NULL`
# filter in fetch_pending().
set -euo pipefail

module load slurm/alpine
module load uv

PROJECT_ROOT="/projects/${USER}/pindorama/repo"
SCRATCH_ROOT="/scratch/alpine/${USER}/pindorama"

mkdir -p "${SCRATCH_ROOT}/logs"

cd "${PROJECT_ROOT}"

uv sync --frozen

uv run python -m pindorama.triage \
  --db "${SCRATCH_ROOT}/metadata.sqlite"
