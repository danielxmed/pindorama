#!/bin/bash
#SBATCH --job-name=pindorama-ocr-chandra
#SBATCH --account=ucb-general
#SBATCH --partition=aa100
#SBATCH --qos=normal
#SBATCH --time=24:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=10
#SBATCH --gres=gpu:1
#SBATCH --array=0-7
#SBATCH --mem=64G
#SBATCH --output=/scratch/alpine/%u/pindorama/logs/ocr-chandra-%A_%a.out
#SBATCH --error=/scratch/alpine/%u/pindorama/logs/ocr-chandra-%A_%a.err
#
# Stage 4c — fallback OCR via datalab-to/chandra served by vLLM.
# Smaller fanout (8 shards) than dots.ocr because the input is the chandra-queue
# of pages flagged by per-page confidence — typically a small subset of the
# corpus. See ADR-0004 for tiering rationale.
#
# Replaces only the flagged pages in <scratch>/extracted_text/<sha256>.txt;
# updates extraction_method='chandra_ocr' for any doc that had ≥1 page replaced.
set -euo pipefail

module load slurm/alpine

mkdir -p "/scratch/alpine/${USER}/pindorama/logs"

cd "${SLURM_SUBMIT_DIR}"

nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv 1>&2

echo "ocr_chandra.sh: pipeline entry point not implemented yet" 1>&2
exit 64

# After Stage 4c lands:
#   uv run python -m pindorama.extract_ocr_chandra     --db /scratch/alpine/$USER/pindorama/metadata.sqlite     --shard-id ${SLURM_ARRAY_TASK_ID}     --shard-count ${SLURM_ARRAY_TASK_COUNT}     --scratch ${SLURM_SCRATCH}     --output-dir /scratch/alpine/$USER/pindorama/extracted_text
