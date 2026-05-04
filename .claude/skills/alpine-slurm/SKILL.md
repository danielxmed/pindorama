---
name: alpine-slurm
description: Use when writing or modifying any SLURM submit script (sbatch), sinteractive command, or job-array directive for the CURC Alpine cluster. Also use when explaining partition / QoS / account choices, computing time-limit and memory budgets, or debugging squeue status codes for this project.
---

# Alpine SLURM patterns

This skill encodes the project-specific patterns for SLURM on CURC Alpine. **Do not restate cluster facts here** — point at `docs/curc/` and only add what is project-specific.

## Authoritative references (read on demand)

- `docs/curc/clusters/alpine/quick-start.md` — partitions, QoS, time/resource limits.
- `docs/curc/clusters/alpine/alpine-hardware.md` — node specs (cores, RAM, GPUs per node).
- `docs/curc/clusters/alpine/important-notes.md` — etiquette, login-node policy.
- `docs/curc/clusters/alpine/allocations.md` — Trailhead Auto-Allocation; this project uses `--account=ucb-general`.
- `docs/curc/running-jobs/{batch-jobs,interactive-jobs,job-resources,job-arrays,slurm-commands,squeue-status-codes}.md`.
- `docs/curc/open_ondemand/vs_code-server.md` — interactive sessions are 1-node max (≤3× A100).
- `docs/curc/getting_started/logging-in.md` — `ssh dame9177@login.rc.colorado.edu`, Duo MFA.

## Project-specific header conventions

These are the canonical headers for `slurm/*.sh` in this repo. Do not invent variants without an ADR.

- **Account:** always `--account=ucb-general` (Trailhead Auto-Allocation).
- **CPU work** (scrape, download, triage, native extraction, post-processing): partition `amilan`, QoS `normal` (≤24h) or `long` (≤7d).
- **GPU production work** (OCR via vLLM): partition `aa100`, QoS `normal`, `--gres=gpu:1`. Use `--array=0-N` to fan out — each task takes one A100, one shard.
- **GPU debug iteration** (≤1h, near-instant queue): partition `atesting_a100`, QoS `testing`, `--gres=gpu:1`. Use this while developing OCR scripts before scaling out on `aa100`.
- **Logs:** `--output=/scratch/alpine/%u/pindorama/logs/<stage>-%j.out` and matching `.err`. Create the `logs/` dir in advance (idempotent: `mkdir -p`).

## Sizing rules of thumb (project-specific)

- The 21-GPU figure is a **simultaneous-job cap**, not a reservation. Size `--array=0-N` to leave headroom for shared cluster usage. Start with `--array=0-19` for `aa100` and tune.
- A100 VRAM is 40 or 80 GB depending on the node — query at runtime with `nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits`. Never hardcode.
- Per-node specs (from `docs/curc/clusters/alpine/alpine-hardware.md`): `aa100` ≈ 64 cores / 256 GB RAM / 3× A100. `--cpus-per-task=10` is a conservative slice for one-GPU jobs and leaves CPU headroom for sibling tasks on the same node.

## Common pitfalls (observed in CURC docs, transcribed here)

- Login nodes are **not** for sustained workloads. Run anything beyond a few seconds via `sbatch`.
- `module load slurm/alpine` is required before any `sbatch`/`squeue` invocation.
- `$SLURM_SCRATCH` is per-job NVMe and wiped at job end — use it for temporary page rasterizations, not for outputs you want to keep.
- `/scratch/alpine/$USER` is fast but **purge-eligible**. Persistent artifacts go to `/projects/$USER/pindorama` or PetaLibrary (see `docs/curc/petalibrary/`).
- `requeue` semantics: design every stage to be idempotent (check the SQLite metadata DB before doing work) so a SLURM kill or requeue loses ≤1 doc.

## Submission pattern

```bash
module load slurm/alpine
mkdir -p /scratch/alpine/$USER/pindorama/logs
sbatch slurm/<stage>.sh
squeue -u $USER  # see docs/curc/running-jobs/squeue-status-codes.md for codes
```

## When this skill is NOT what you want

- Pure cluster facts (partitions, hardware, OnDemand setup) → read `docs/curc/` directly. Don't paraphrase here.
- Writing OCR inference loops → use the `vllm-ocr` skill, then come back here for the SLURM wrapper.
