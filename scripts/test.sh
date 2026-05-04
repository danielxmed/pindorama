#!/usr/bin/env bash
# test.sh — pytest, quiet, fail on first miss to keep feedback tight.
set -euo pipefail
cd "$(dirname "$0")/.."

if command -v uv >/dev/null 2>&1 && [ -f pyproject.toml ]; then
  exec uv run --no-sync pytest -q
fi

if ! command -v pytest >/dev/null 2>&1; then
  echo "test.sh: pytest not on PATH; falling back to 'python3 -m unittest discover'." 1>&2
  exec python3 -m unittest discover -s tests -t .
fi

pytest -q
