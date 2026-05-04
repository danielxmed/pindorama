# Runbook — Recover from a failed pipeline stage

When a SLURM job fails (`sacct` shows `FAILED` or `TIMEOUT`) or a stage exits non-zero on a login-node smoke test, follow this procedure. The pipeline is idempotent by design — recovery is mostly "look at the symptom, run the stage again."

## 0. Don't panic

Pindorama state is filesystem-evident. The two truth sources are:

1. The bytes in `<scratch>/raw_pdfs/<sha256>.pdf` — the source of all downstream artifacts.
2. `<scratch>/metadata.sqlite` — the per-doc state machine.

If `metadata.sqlite` is corrupt or lost, restore from the most recent `<scratch>/backups/metadata_<date>.sqlite`. If both are gone, the filesystem can rebuild the DB by re-hashing the raw PDFs and re-running scrape (the catalog is reproducible).

## 1. Find the failing job's logs

```bash
sacct -u $USER --starttime=$(date -d '-2 days' +%F) --format=JobID,JobName%20,State,ExitCode,Start,End
ls -lh /scratch/alpine/$USER/pindorama/logs/<stage>-<jobid>.{out,err}
tail -200 /scratch/alpine/$USER/pindorama/logs/<stage>-<jobid>.err
```

Most common failure types:

| Symptom in `.err` | Diagnosis | Fix |
| --- | --- | --- |
| `OOM`, `killed`, `Out of memory` | VRAM or RAM exceeded | Drop batch size in the OCR script, or shrink `--cpus-per-task` / `--mem` for CPU jobs. Resubmit. |
| `TIMEOUT` | wallclock exceeded `--time` | Either bump `--time` (subject to QoS cap) or shard further with a larger `--array=` count. |
| Network / 5xx from `dominiopublico.gov.br` | upstream flake | Resubmit; the scraper retries with exponential backoff. If persistent, slow the rate (≤2 req/s is the cap; you can drop to 1). |
| `sqlite3.OperationalError: database is locked` | concurrent writers | Confirm WAL mode is on; consider serializing writes through a single coordinator process. |
| `ModuleNotFoundError` | env not synced | `uv sync` in the repo root, then resubmit. |
| `CUDA initialization: ...` | GPU not allocated or driver mismatch | Verify `--gres=gpu:1` and partition `aa100` / `atesting_a100`. |

## 2. Inspect DB state for the affected docs

```bash
sqlite3 /scratch/alpine/$USER/pindorama/metadata.sqlite <<'SQL'
SELECT extraction_status, extraction_method, COUNT(*)
FROM works
GROUP BY 1, 2
ORDER BY 3 DESC;
SQL
```

If a stage left rows in a transitional state (e.g. `extraction_status='in_progress'` because the job was killed mid-doc), reset them so the next run picks them up:

```sql
-- DRY RUN FIRST. Always inspect the rows you're about to mutate.
SELECT id, file_path, extraction_status FROM works WHERE extraction_status='in_progress';

-- If the rowset looks right:
UPDATE works
SET extraction_status='pending', extraction_method=NULL
WHERE extraction_status='in_progress';
```

## 3. Resubmit

```bash
module load slurm/alpine
sbatch slurm/<stage>.sh
```

The stage's entry point checks DB state and skips already-`done` rows, so a partial failure costs at most the wall-time of the work since the last completed doc.

## 4. If the symptom is "the bytes are wrong"

If a downstream stage is producing garbage and the suspicion is that the source PDF or extracted text is corrupt:

1. Recompute the PDF's SHA-256: `shasum -a 256 <scratch>/raw_pdfs/<sha256>.pdf`. If it does not match the filename, the file was truncated — delete it and re-set the row's `download_status='pending'`.
2. Re-run extraction by setting `extraction_status='pending'` for that row and resubmitting the stage.

## 5. If `metadata.sqlite` is truly broken

```bash
ls /scratch/alpine/$USER/pindorama/backups/
cp /scratch/alpine/$USER/pindorama/backups/metadata_<latest>.sqlite \
   /scratch/alpine/$USER/pindorama/metadata.sqlite
sqlite3 /scratch/alpine/$USER/pindorama/metadata.sqlite "PRAGMA integrity_check;"
```

If even the latest backup is bad, rebuild from the catalog: re-run Stage 1 (the scrape is reproducible because the source URLs are deterministic). Stages 2+ will re-do work but skip already-on-disk content (Stage 2 because of content-addressed names, Stage 4 because cleaned text files exist on disk and can be re-imported into the rebuilt DB by a small recovery script).

## 6. When to ask Daniel before retrying

- Same failure repeats after one resubmit. Stop and surface the `.err` tail.
- A stage's resubmit produces *different* output for the same input doc. This indicates non-determinism worth investigating before continuing.
- `/scratch/alpine` shows signs of imminent purge (CURC mass-storage warnings).
