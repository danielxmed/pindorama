# Runbook — Connect to CURC Alpine

For Daniel's reference and for any new agent that needs to run pipeline jobs on the cluster. This is a runbook, not a guide — terse and procedural.

## Prerequisite

- Active CU Boulder identikey: `dame9177` (corresponding to `dame9177@colorado.edu`).
- Duo MFA enrolled.

## SSH

```bash
ssh dame9177@login.rc.colorado.edu
# Approve the Duo push when prompted.
```

If the connection hangs at the password prompt, see `docs/curc/getting_started/logging-in.md`.

## Initial environment setup (first time on a fresh login node)

```bash
module load slurm/alpine
module avail                       # see what else is offered; we don't load others routinely
mkdir -p /scratch/alpine/$USER/pindorama/{logs,raw_pdfs,extracted_text,cleaned_text,backups}
mkdir -p /projects/$USER/pindorama
```

The repo lives wherever you cloned it (typically `~/pindorama` or `/projects/$USER/pindorama/repo`). Code is on `/home` or `/projects` (persistent); pipeline outputs live on `/scratch/alpine` (purge-eligible, fast); per-job temps live in `$SLURM_SCRATCH` (NVMe, wiped at job end).

## Python environment (uv)

```bash
cd <repo-root>
# uv install (one-time): see docs/curc/software/uv.md.
uv sync                            # creates .venv and installs dev deps
uv run pytest                      # smoke
```

Do **not** `pip install` into the system Python or into a CURC-provided module. `uv` keeps the env in `.venv/` inside the repo, isolated.

## Submit a job

Always from a login node, never from another job:

```bash
module load slurm/alpine           # reload after every fresh login
sbatch slurm/<stage>.sh
squeue -u $USER                    # status; see docs/curc/running-jobs/squeue-status-codes.md
```

For interactive iteration on GPU OCR scripts:

```bash
sinteractive --partition=atesting_a100 --gres=gpu:1 --time=01:00:00 --account=ucb-general --qos=testing
# Drops you into a 1× A100 MIG slice for ≤1h. Near-instant queue.
```

## Tear down / disconnect

```bash
exit                               # leave the login node
```

Active `sbatch` jobs continue running after disconnect.

## Where things live (quick recap)

| Path | Persistence | Use for |
| --- | --- | --- |
| `~` / `/home/$USER` | persistent, small | dotfiles, ssh keys |
| `/projects/$USER/pindorama` | persistent, larger | repo clone, final cleaned corpus, dataset-card drafts |
| `/scratch/alpine/$USER/pindorama` | fast, **purge-eligible** | raw PDFs, extracted text, intermediate state, `metadata.sqlite` |
| `$SLURM_SCRATCH` | per-job NVMe, wiped at job end | rendered page images, vLLM cache |

## Common issues

- **`module: command not found`** — you are on the wrong host. `ssh login.rc.colorado.edu` again.
- **`sbatch: error: invalid account`** — the script is missing `--account=ucb-general`.
- **Job stuck in `PD` (pending) on `aa100` for hours** — the 21-GPU cap is shared across the user's jobs. `squeue -u $USER -o "%.18i %.9P %.8j %.8T %.10M %.6D %R"` and trim `--array=` size if needed.
