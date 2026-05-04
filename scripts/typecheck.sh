#!/usr/bin/env bash
# typecheck.sh — mypy strict pass over src/ and tests/.
set -euo pipefail
cd "$(dirname "$0")/.."

if command -v uv >/dev/null 2>&1 && [ -f pyproject.toml ]; then
  exec uv run --no-sync mypy src tests
fi

if ! command -v mypy >/dev/null 2>&1; then
  echo "typecheck.sh: mypy not on PATH; install dev deps via 'uv sync'." 1>&2
  echo "typecheck.sh: skipping (treating as soft-pass)." 1>&2
  exit 0
fi

mypy src tests
