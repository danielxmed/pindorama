#!/bin/bash
#SBATCH --job-name=pindorama-scrape
#SBATCH --account=ucb-general
#SBATCH --partition=amilan
#SBATCH --qos=normal
#SBATCH --time=24:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=8G
#SBATCH --output=/scratch/alpine/%u/pindorama/logs/scrape-%j.out
#SBATCH --error=/scratch/alpine/%u/pindorama/logs/scrape-%j.err
#
# Stages 1 & 2 — catalog scrape + PDF download.
# Both are CPU- and network-bound. Login nodes are NOT for sustained workloads;
# always submit this via sbatch. See docs/runbooks/connect-to-alpine.md.
#
# Resubmits are safe: idempotency is enforced by SQLite + content-addressed
# storage (ADR-0002, ADR-0003). A killed job loses ≤1 doc.
set -euo pipefail

module load slurm/alpine

# Ensure logs dir exists (sbatch will fail to write the .out otherwise on a
# first run after a fresh scratch wipe).
mkdir -p "/scratch/alpine/${USER}/pindorama/logs"
mkdir -p "/scratch/alpine/${USER}/pindorama/raw_pdfs"

cd "${SLURM_SUBMIT_DIR}"

# Pipeline entry-point not yet implemented. This template will dispatch to
# src/pindorama/scrape_catalog.py and src/pindorama/download_pdfs.py once
# Stage 1/2 land. For now, fail loud so a misfire is obvious.
echo "scrape_or_download.sh: pipeline entry point not implemented yet" 1>&2
echo "scrape_or_download.sh: see PROGRESS.md for the next concrete action" 1>&2
exit 64

# After Stage 1 lands, replace the lines above with:
#   uv run python -m pindorama.scrape_catalog   --db /scratch/alpine/$USER/pindorama/metadata.sqlite
#   uv run python -m pindorama.download_pdfs    --db /scratch/alpine/$USER/pindorama/metadata.sqlite
