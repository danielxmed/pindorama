#!/bin/bash
#SBATCH --job-name=pindorama-postproc
#SBATCH --account=ucb-general
#SBATCH --partition=amilan
#SBATCH --qos=normal
#SBATCH --time=12:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=32
#SBATCH --mem=128G
#SBATCH --output=/scratch/alpine/%u/pindorama/logs/postproc-%j.out
#SBATCH --error=/scratch/alpine/%u/pindorama/logs/postproc-%j.err
#
# Stage 5 — post-processing & dedup. CPU-only.
# In order: dehyphenation, paragraph reflow, header/footer removal, NFC + smart-quote
# normalization, whitespace, language-ID quality filter, MinHash+LSH near-dup,
# paragraph-level exact dedup. Output: <scratch>/cleaned_text/<sha256>.txt.
#
# 128 GB RAM is sized for MinHash+LSH (datasketch) over ~20k docs; the LSH
# index can grow large. Tune down once measured.
set -euo pipefail

module load slurm/alpine

mkdir -p "/scratch/alpine/${USER}/pindorama/logs"
mkdir -p "/scratch/alpine/${USER}/pindorama/cleaned_text"

cd "${SLURM_SUBMIT_DIR}"

echo "postprocess.sh: pipeline entry point not implemented yet" 1>&2
exit 64

# After Stage 5 lands:
#   uv run python -m pindorama.postprocess     --db /scratch/alpine/$USER/pindorama/metadata.sqlite     --workers ${SLURM_NTASKS}
