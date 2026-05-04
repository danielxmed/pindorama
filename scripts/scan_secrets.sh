#!/usr/bin/env bash
# scan_secrets.sh — secret scanner.
#
# Modes:
#   scan_secrets.sh --staged     # scan `git diff --cached` (used by guard_bash.sh on git commit)
#   scan_secrets.sh --tree       # scan all tracked files in the repo
#   scan_secrets.sh <file>...    # scan listed files
#
# Exit 0 = clean. Exit 1 = secret found (printed to stderr).
#
# Patterns covered:
#   - HuggingFace tokens:  hf_[A-Za-z0-9]{32,}
#
# Deliberately narrow. The brief specifies this exact regex; broader heuristics
# (e.g. "any HF_TOKEN= with a value") were tried and produced false positives
# on documentation that legitimately mentions the variable name. If a new
# secret class needs coverage, add it deliberately and update this header.
set -euo pipefail

PATTERN_HF='\bhf_[A-Za-z0-9]{32,}\b'

scan_blob() {
  local label="$1" blob="$2"
  if printf '%s' "$blob" | grep -E -- "$PATTERN_HF" >/dev/null 2>&1; then
    echo "scan_secrets.sh: HuggingFace token detected in $label" 1>&2
    return 1
  fi
  return 0
}

case "${1:-}" in
  --staged)
    diff="$(git diff --cached --no-color)" || true
    scan_blob "staged diff" "$diff"
    ;;
  --tree)
    rc=0
    while IFS= read -r f; do
      [ -f "$f" ] || continue
      case "$f" in
        .env.example) continue ;;
      esac
      content="$(cat "$f")"
      if ! scan_blob "$f" "$content"; then rc=1; fi
    done < <(git ls-files)
    exit "$rc"
    ;;
  --help|-h|"")
    sed -n '2,12p' "$0"
    exit 0
    ;;
  *)
    rc=0
    for f in "$@"; do
      [ -f "$f" ] || continue
      content="$(cat "$f")"
      if ! scan_blob "$f" "$content"; then rc=1; fi
    done
    exit "$rc"
    ;;
esac
