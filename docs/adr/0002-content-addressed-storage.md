# ADR-0002 — Content-addressed storage for downloaded PDFs

**Status:** Accepted
**Date:** 2026-05-04

## Context

Stage 2 downloads tens of thousands of PDFs from `dominiopublico.gov.br` into `<scratch>/raw_pdfs/`. The pipeline must be:

1. **Idempotent.** A SLURM kill at hour 23:59 must be safe to retry; we cannot afford to re-download a PDF we already have.
2. **Resumable.** Catalog scrapes and downloads run as separate jobs; the catalog can update; we need to detect when a "new" URL points to a file we already have.
3. **Deduplicating at the byte level.** The catalog has duplicate listings (same edition under multiple categories). Two URLs producing the same bytes must collapse to one downstream artifact.
4. **Auditable.** Every published `text` row must be traceable back to the exact bytes it was extracted from.

## Decision

Store every downloaded PDF at `<scratch>/raw_pdfs/<sha256>.pdf`, where `<sha256>` is the lowercase hex SHA-256 of the file contents. The download routine:

1. Streams bytes to a `.tmp` file under the same directory, computing SHA-256 incrementally.
2. On completion, `os.replace`s into `<sha256>.pdf` (atomic on POSIX same-filesystem).
3. Updates `works.file_path` and `works.file_sha256` in `metadata.sqlite`.
4. If the destination already exists, the download is skipped (idempotency); the catalog row is updated to point at the existing file.

Extracted text follows the same convention: `<scratch>/extracted_text/<sha256>.txt` and `<scratch>/cleaned_text/<sha256>.txt`. The HuggingFace dataset's `id` column is also the SHA-256.

## Consequences

- **Pro:** Idempotency is filesystem-level, not application-level. A killed job restarts cheaply.
- **Pro:** Byte-level dedup is free.
- **Pro:** A reader of the published dataset can hash a downloaded PDF and immediately find the corresponding row.
- **Con:** Filenames are not human-readable. Any human inspection requires `metadata.sqlite` joins. Acceptable.
- **Con:** Renaming a PDF after download (e.g. to fix encoding) is meaningless — the hash changes. So we never rename; corrections happen in the SQLite metadata, never in the filesystem.

## Alternatives considered

- **Sequential integer ids.** Rejected: not idempotent across reruns; offers no dedup; not auditable.
- **URL-derived filenames.** Rejected: same content can have multiple URLs (mirrors, redirects); URL changes upstream would silently re-download.
- **UUID per row.** Rejected: not deduplicating, not auditable from the bytes.
