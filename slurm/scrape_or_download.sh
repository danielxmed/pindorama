#!/bin/bash
#SBATCH --job-name=pindorama-scrape
#SBATCH --account=ucb-general
#SBATCH --partition=amilan
#SBATCH --qos=normal
#SBATCH --time=02:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=8G
#SBATCH --output=/scratch/alpine/%u/pindorama/logs/scrape-%j.out
#SBATCH --error=/scratch/alpine/%u/pindorama/logs/scrape-%j.err
#
# Stage 1 — catalog scrape of dominiopublico.gov.br.
# CPU + network bound. Login nodes are NOT for sustained workloads;
# always submit this via sbatch. See docs/runbooks/connect-to-alpine.md.
#
# Resubmits are safe: idempotency is enforced by SQLite UNIQUE(catalog_url)
# (ADR-0003). A killed job loses ≤1 page of work.
#
# The site is fronted by Cloudflare managed challenge, so we use Playwright
# headless Chromium (vs. plain httpx). Browser cache lives under /projects
# rather than ~/.cache to avoid blowing the 2 GiB /home quota.
set -euo pipefail

module load slurm/alpine
module load uv

PROJECT_ROOT="/projects/${USER}/pindorama/repo"
SCRATCH_ROOT="/scratch/alpine/${USER}/pindorama"
PLAYWRIGHT_CACHE="/projects/${USER}/pindorama/playwright-browsers"

mkdir -p "${SCRATCH_ROOT}/logs"
mkdir -p "${SCRATCH_ROOT}/raw_pdfs"
mkdir -p "${PLAYWRIGHT_CACHE}"

cd "${PROJECT_ROOT}"

# Resolve the deps lockfile if needed (idempotent).
uv sync --frozen

# Tell Playwright to keep its Chromium under /projects (250 GiB quota).
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_CACHE}"

# Install Chromium only if no version is cached yet. The binary is
# content-addressed under PLAYWRIGHT_BROWSERS_PATH/chromium-<v>; the install
# command itself spawns a uv resolution + a CDN probe even when there's
# nothing to download, so a transient network blip would still kill the job.
# We deliberately do NOT pass --with-deps (no root on Alpine). If launch
# fails for missing system libs, fall back to apptainer (docs/curc/software/apptainer.md).
if ! compgen -G "${PLAYWRIGHT_CACHE}/chromium-*" >/dev/null; then
  uv run python -m playwright install chromium
fi

# Stage 1: walk the catalog.
uv run python -m pindorama.scrape_catalog \
  --db "${SCRATCH_ROOT}/metadata.sqlite"

# Stage 2 entry point lands here once the downloader is implemented.
# Until then this script ends after Stage 1.
