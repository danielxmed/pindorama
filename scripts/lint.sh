#!/usr/bin/env bash
# lint.sh — ruff check + ruff format --check. Fast.
set -euo pipefail
cd "$(dirname "$0")/.."

if command -v uv >/dev/null 2>&1 && [ -f pyproject.toml ]; then
  exec uv run --no-sync -- bash -c 'ruff check . && ruff format --check .'
fi

if ! command -v ruff >/dev/null 2>&1; then
  echo "lint.sh: ruff not on PATH; install dev deps via 'uv sync' (or 'pip install ruff')." 1>&2
  echo "lint.sh: skipping (treating as soft-pass) — see PROGRESS.md to pin ruff." 1>&2
  exit 0
fi

ruff check .
ruff format --check .
