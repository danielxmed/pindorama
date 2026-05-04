# ADR-0003 — SQLite as the single source of truth

**Status:** Accepted
**Date:** 2026-05-04

## Context

The pipeline has eight stages that produce and consume per-document metadata: scrape status, file path, file hash, file size, triage classification, extraction status, extraction method, token count, errors. The pipeline is also re-runnable, idempotent, and runs as separate SLURM jobs that may be killed and retried at any time.

We need a state store that is:

1. **Single-file** — one `metadata.sqlite` is easy to back up, copy off-cluster, and inspect.
2. **Concurrent-safe** for the modest write rates we see (≪100 writes/s after enabling WAL).
3. **Queryable** — SQL is the right language for "give me all docs in stage 4 status `needs_ocr` with size < 10 MB".
4. **Available without a server** — we do not want to operate Postgres on the cluster, and `/scratch/alpine` is a shared filesystem where running a long-lived daemon is awkward.
5. **Recoverable** — a corrupted or lost DB must not block re-running the pipeline; the filesystem (content-addressed PDFs) is the secondary truth source from which the DB can be rebuilt.

## Decision

Use **SQLite** as the single source of truth for all per-document metadata. `metadata.sqlite` lives at `<scratch>/metadata.sqlite`. Connection mode: `journal_mode=WAL`, `foreign_keys=ON`.

The schema is the `CREATE TABLE works` block in `src/pindorama/db.py`. Every stage entry point:

1. Opens the DB.
2. Queries for rows in the appropriate input state.
3. Does work.
4. Writes back updated state in a transaction.

A nightly cron-like `sbatch` backs up the DB to `<scratch>/backups/metadata_<date>.sqlite`. Loss tolerance: ≤24h of metadata, recoverable from filesystem evidence (recompute hashes, re-triage the small minority of affected docs).

No ORM. Hand-rolled `sqlite3` is enough; an ORM would crowd the model's context and add indirection nobody benefits from.

## Consequences

- **Pro:** Each stage is implemented as a small Python script with one DB query and one DB write. Easy to read, easy to test.
- **Pro:** `sqlite3 metadata.sqlite "SELECT extraction_method, COUNT(*) FROM works GROUP BY 1"` is trivially available for monitoring.
- **Pro:** Idempotency is just "did the row's status change yet?" — no distributed locking, no transactional state machine.
- **Con:** Concurrent writers from multiple SLURM tasks on `aa100` are serialized by SQLite's WAL. Acceptable: write rates are bounded by OCR throughput, which is the bottleneck anyway.
- **Con:** Schema migrations require care. Mitigated by the rule "never mutate a column; add a new one and migrate in a second pass."

## Alternatives considered

- **Postgres / RDS.** Rejected: cluster-side daemon ops; overkill for ~20k rows.
- **Plain JSON files per doc.** Rejected: cross-doc queries (top-50 authors, distribution by year) require loading everything into memory.
- **Parquet as state.** Rejected: Parquet is for the *output* dataset (Stage 7); using it as live state means rewriting the file on every update, which is awful at our update rate.
