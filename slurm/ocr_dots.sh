#!/bin/bash
#SBATCH --job-name=pindorama-ocr-dots
#SBATCH --account=ucb-general
#SBATCH --partition=aa100
#SBATCH --qos=normal
#SBATCH --time=24:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=10
#SBATCH --gres=gpu:1
#SBATCH --array=0-19
#SBATCH --mem=64G
#SBATCH --output=/scratch/alpine/%u/pindorama/logs/ocr-dots-%A_%a.out
#SBATCH --error=/scratch/alpine/%u/pindorama/logs/ocr-dots-%A_%a.err
#
# Stage 4b — primary OCR via rednote-hilab/dots.ocr served by vLLM.
# Array job: 20 shards. Tune --array= up or down based on cluster contention;
# the user-wide aa100 cap is 21 simultaneous GPUs (see docs/curc/clusters/alpine/).
#
# Each task:
#   1) Loads dots.ocr in vLLM with gpu_memory_utilization tuned to the actual VRAM
#      (queried at runtime via nvidia-smi — never hardcoded).
#   2) Pulls a partition of `needs_ocr` PDFs from metadata.sqlite based on
#      $SLURM_ARRAY_TASK_ID and $SLURM_ARRAY_TASK_COUNT.
#   3) Renders pages at 200–300 DPI under $SLURM_SCRATCH (per-job NVMe).
#   4) Batches inference; per-page confidence proxy flags pages for chandra fallback.
#   5) Writes per-doc text atomically; updates SQLite only after success.
#
# See ADR-0004 for the tiering rationale and skill `vllm-ocr` for batch-sizing logic.
set -euo pipefail

module load slurm/alpine

mkdir -p "/scratch/alpine/${USER}/pindorama/logs"
mkdir -p "/scratch/alpine/${USER}/pindorama/extracted_text"

cd "${SLURM_SUBMIT_DIR}"

# Diagnostic: log the GPU we got. Confirms VRAM size at runtime — never
# hardcode A100=80GB or A100=40GB; the cluster has both.
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv 1>&2

echo "ocr_dots.sh: pipeline entry point not implemented yet" 1>&2
exit 64

# After Stage 4b lands:
#   uv run python -m pindorama.extract_ocr_dots     --db /scratch/alpine/$USER/pindorama/metadata.sqlite     --shard-id ${SLURM_ARRAY_TASK_ID}     --shard-count ${SLURM_ARRAY_TASK_COUNT}     --scratch ${SLURM_SCRATCH}     --output-dir /scratch/alpine/$USER/pindorama/extracted_text
