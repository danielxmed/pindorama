#!/usr/bin/env bash
# guard_filewrite.sh — PreToolUse hook for Write / Edit / MultiEdit tools.
# Reads hook JSON from stdin, extracts the target file_path, and blocks if the
# write target is outside the allowed roots:
#   - the repo itself ($CLAUDE_PROJECT_DIR)
#   - /scratch/alpine/<user>/pindorama
#   - /projects/<user>/pindorama
#
# Note: on Daniel's Mac during local development, only the repo root is real;
# the cluster paths are still allowed in case the agent works on a mounted
# scratch via SSHFS. Failing closed (block) is the safe default for unknown
# locations.
set -euo pipefail

PAYLOAD="$(cat)"

FILE_PATH="$(printf '%s' "$PAYLOAD" | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)
ti = data.get("tool_input") or {}
print(ti.get("file_path", ""), end="")
')"

if [ -z "$FILE_PATH" ]; then
  exit 0
fi

# Resolve to absolute. If the file does not yet exist (a new write), resolve
# the parent directory and re-attach the basename.
abs_path() {
  local p="$1"
  if [ -e "$p" ]; then
    python3 -c "import os,sys; print(os.path.realpath(sys.argv[1]))" "$p"
  else
    local dir base
    dir="$(dirname "$p")"
    base="$(basename "$p")"
    [ -e "$dir" ] || { echo "$p"; return; }
    python3 -c "import os,sys; print(os.path.join(os.path.realpath(sys.argv[1]), sys.argv[2]))" "$dir" "$base"
  fi
}

ABS="$(abs_path "$FILE_PATH")"
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(pwd)}"
PROJECT_DIR_ABS="$(python3 -c "import os,sys; print(os.path.realpath(sys.argv[1]))" "$PROJECT_DIR")"

USER_NAME="${USER:-$(id -un)}"
ALLOWED_PREFIXES=(
  "$PROJECT_DIR_ABS"
  "/scratch/alpine/$USER_NAME/pindorama"
  "/projects/$USER_NAME/pindorama"
)

allowed=0
for prefix in "${ALLOWED_PREFIXES[@]}"; do
  case "$ABS" in
    "$prefix"|"$prefix"/*) allowed=1; break ;;
  esac
done

if [ "$allowed" -ne 1 ]; then
  echo "guard_filewrite.sh: BLOCKED — refusing to write outside allowed roots." 1>&2
  echo "  target:   $ABS" 1>&2
  echo "  allowed:  ${ALLOWED_PREFIXES[*]}" 1>&2
  echo "  If this is intentional, either move the work into the repo or update" 1>&2
  echo "  scripts/guard_filewrite.sh's ALLOWED_PREFIXES list and document why." 1>&2
  exit 2
fi

exit 0
