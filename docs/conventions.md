# Pindorama — Conventions

Short, opinionated, enforced by sensors where possible. Topics that drift unenforced eventually stop being honored; topics that the linter / typechecker enforces stay correct without effort.

## Python style

- **Versions:** Python 3.11+. Tested on 3.11 and 3.12.
- **Linter:** `ruff` (config in `pyproject.toml`). Line length 100.
- **Formatter:** `ruff format`. No black, no isort separately.
- **Imports:** sorted by ruff's `I` rule. No relative imports across the `pindorama` package — always `from pindorama.X import Y`.
- **Type hints:** required on every public function and method (modules, classes, top-level helpers). Internal lambdas / list comprehensions exempt. `mypy --strict` is the gate.
- **Future annotations:** every module starts with `from __future__ import annotations`. This keeps `list[int]` / `X | None` legal everywhere and avoids forward-reference hassles.
- **Docstrings:** one-line module docstring at the top of each `src/pindorama/*.py` describing what stage / role the file plays. Function docstrings only when the signature is non-obvious.
- **No print().** Use `logging`. The hook tool will not enforce this, but the reviewer subagent will flag it.

## Logging

- Stdlib `logging`, not `loguru` or third-party.
- Configure once, at the entry point of each script (no logger setup inside library code).
- Format: **one JSON object per line**, suitable for `jq`. Required fields:
  - `ts` — ISO-8601 UTC.
  - `level` — `INFO` / `WARNING` / `ERROR`.
  - `stage` — `scrape` / `download` / `triage` / `extract_native` / `extract_dots` / `extract_chandra` / `postprocess` / `analyze` / `package`.
  - `doc_id` — SHA-256 of the source PDF when applicable, else `null`.
  - `action` — short verb: `start`, `done`, `skip`, `retry`, `fail`.
  - `status` — `ok` / `error` / `partial`.
  - `duration_ms` — int, the action's wall-clock time when meaningful.
  - `extra` — optional dict for stage-specific fields.
- Example:
  ```json
  {"ts":"2026-05-04T18:32:11Z","level":"INFO","stage":"download","doc_id":"3f9c…","action":"done","status":"ok","duration_ms":1843}
  ```
- Filter pattern: `cat slurm/logs/*.out | jq -c 'select(.stage=="download" and .status=="error")'`.

## Idempotency

Every stage entry point starts with:

1. Open `metadata.sqlite`.
2. Query rows in the input state.
3. **Skip** rows already in the output state. Log `action=skip,status=ok`.
4. Do work on the rest.
5. Write back state in a transaction.

A SLURM kill at any point must lose ≤1 doc. This is a behavioral contract — the reviewer subagent will block diffs that violate it.

## File naming

- **Content-addressed:** `<sha256>.pdf`, `<sha256>.txt`. Lowercase hex, full 64 chars.
- **SLURM logs:** `<stage>-<jobid>.out` and `.err`, in `<scratch>/logs/`.
- **Backups:** `metadata_<YYYY-MM-DD>.sqlite` in `<scratch>/backups/`.
- **No spaces, no uppercase** in any pipeline-emitted filename. Spaces in the original PDF title go in `works.title`, not in filenames.

## Dependencies

- Pinned in `pyproject.toml` (lockfile via `uv sync`). The bootstrap commit ships with empty pin lists pending Daniel's confirmation of canonical versions (PROGRESS.md verification question 3).
- New dependencies require justification: each addition is a small ADR-worthy decision. If you find yourself adding a library to do something the stdlib could do in 20 lines, prefer the 20 lines.
- Direct GitHub commit pins for `dots.ocr` and `chandra-ocr`. Record the commit hash both in `pyproject.toml` and in the corresponding stage's docstring.

## Testing

- `pytest`. Tests live in `tests/`. One test module per `src/pindorama/<module>.py`.
- Smoke tests minimum: every module that defines functions must have at least one test that imports the module and exercises the happy path of one function.
- Heavy tests (real OCR inference, real network) are marked `@pytest.mark.slow` and skipped by default. Run with `pytest -m slow` when validating before a release.
- No mocking of the SQLite layer — use an in-memory DB (`":memory:"`) or a tmpdir-scoped file. SQL bugs caught at integration time are cheaper than at production time.

## Git / PR hygiene

- Branch off `main`. PR with a one-paragraph "what / why / sensors-green-evidence".
- Force-push to `main` is hook-blocked.
- Commits aim for atomic and reviewable. Squash on merge if the branch grew messy.
- Every PR must end on a green `bash scripts/check.sh`.
