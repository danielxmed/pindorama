#!/bin/bash
#SBATCH --job-name=pindorama-triage
#SBATCH --account=ucb-general
#SBATCH --partition=amilan
#SBATCH --qos=normal
#SBATCH --time=06:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=32
#SBATCH --mem=64G
#SBATCH --output=/scratch/alpine/%u/pindorama/logs/triage-%j.out
#SBATCH --error=/scratch/alpine/%u/pindorama/logs/triage-%j.err
#
# Stage 3 — triage. CPU-only, parallelized via multiprocessing.Pool.
# For each downloaded PDF: open with pymupdf, sample first 5 pages, compute the
# native-quality score, classify as native | needs_ocr | unusable.
#
# 32 tasks per node fits aa milan node specs comfortably with 64 GB mem.
set -euo pipefail

module load slurm/alpine

mkdir -p "/scratch/alpine/${USER}/pindorama/logs"

cd "${SLURM_SUBMIT_DIR}"

echo "triage.sh: pipeline entry point not implemented yet" 1>&2
exit 64

# After Stage 3 lands:
#   uv run python -m pindorama.triage   --db /scratch/alpine/$USER/pindorama/metadata.sqlite   --workers ${SLURM_NTASKS}
