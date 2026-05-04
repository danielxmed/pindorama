#!/usr/bin/env bash
# check.sh — aggregator sensor.
#
# Modes:
#   bash scripts/check.sh           # full pass: lint + typecheck + tests + secret scan
#   bash scripts/check.sh --fast    # post-edit fast pass: lint only
#                                     (called from .claude/hooks.json PostToolUse)
#
# Exit 0 = all green. Exit non-zero = at least one sensor failed; stderr names which.
#
# Inner-loop budget: --fast must stay under ~3 seconds. Full must stay under ~30s
# until the project grows big enough to warrant splitting.
set -euo pipefail

cd "$(dirname "$0")/.."

mode="${1:-full}"

run() {
  local label="$1"; shift
  printf '\n[check] %s ...\n' "$label" 1>&2
  if "$@"; then
    printf '[check] %s ... ok\n' "$label" 1>&2
  else
    rc=$?
    printf '[check] %s ... FAILED (exit %s)\n' "$label" "$rc" 1>&2
    exit "$rc"
  fi
}

# Choose how to invoke each tool. Prefer `uv run` when uv exists and a venv is
# set up; otherwise fall back to direct invocation. This keeps the script
# usable on the cluster (uv-managed) and on Daniel's Mac (either uv or system).
runner=()
if command -v uv >/dev/null 2>&1 && [ -f pyproject.toml ]; then
  runner=(uv run --no-sync)
fi

case "$mode" in
  --fast|fast)
    run "lint (ruff)" bash scripts/lint.sh
    ;;
  --full|full|"")
    run "lint (ruff)" bash scripts/lint.sh
    run "typecheck (mypy)" bash scripts/typecheck.sh
    run "tests (pytest)" bash scripts/test.sh
    run "secret scan" bash scripts/scan_secrets.sh --tree
    ;;
  --help|-h)
    sed -n '2,12p' "$0"
    exit 0
    ;;
  *)
    echo "check.sh: unknown mode '$mode' (use --fast or --full)" 1>&2
    exit 64
    ;;
esac

printf '\n[check] all green\n' 1>&2
