#!/bin/bash
#SBATCH --job-name=pindorama-extract-native
#SBATCH --account=ucb-general
#SBATCH --partition=amilan
#SBATCH --qos=normal
#SBATCH --time=01:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=8G
#SBATCH --output=/scratch/alpine/%u/pindorama/logs/extract_native-%j.out
#SBATCH --error=/scratch/alpine/%u/pindorama/logs/extract_native-%j.err
#
# Stage 4a — native PDF text extraction. CPU-only, sequential.
#
# For every row with triage_status='native_extractable' AND extraction_status IS NULL,
# open the PDF with pymupdf, pull text page-by-page (joined with \f), write to
# <scratch>/extracted_text/<sha>.txt (UTF-8, no BOM), and set
# extraction_status='success' / extraction_method='native' / extracted_path /
# token_count=NULL. Errors record extraction_status='error' + error_log.
#
# Volume: ~2k native PDFs at ~300 ms each → ~10 min wall, well under the 1h
# wall budget. token_count is intentionally left NULL — Stage 6 backfills it.
#
# Resubmits are safe: idempotency comes from the `extraction_status IS NULL`
# filter in fetch_pending().
set -euo pipefail

module load slurm/alpine
module load uv

PROJECT_ROOT="/projects/${USER}/pindorama/repo"
SCRATCH_ROOT="/scratch/alpine/${USER}/pindorama"

mkdir -p "${SCRATCH_ROOT}/logs" "${SCRATCH_ROOT}/extracted_text"

cd "${PROJECT_ROOT}"

uv sync --frozen

uv run python -m pindorama.extract_native \
  --db "${SCRATCH_ROOT}/metadata.sqlite" \
  --extract-dir "${SCRATCH_ROOT}/extracted_text"
