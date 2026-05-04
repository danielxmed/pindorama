#!/usr/bin/env bash
# guard_bash.sh — PreToolUse hook for the Bash tool.
# Reads the Claude Code hook JSON envelope from stdin, extracts the command
# string, and blocks (exit 2) if it matches a Pindorama-specific destructive
# pattern. Otherwise exit 0 (allow).
#
# Why this exists: protect /scratch/alpine/$USER/pindorama, /projects/$USER/pindorama,
# and the user's home from accidental rm -rf. Block git push --force to main and
# block git commit when a HuggingFace token is staged.
#
# Stays narrow: this hook is NOT a style enforcer and NOT a general linter.
set -euo pipefail

# Read hook JSON from stdin.
PAYLOAD="$(cat)"

# Extract the Bash command string. Use python for reliable JSON parsing.
COMMAND="$(printf '%s' "$PAYLOAD" | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)  # malformed payload — fail open
ti = data.get("tool_input") or {}
print(ti.get("command", ""), end="")
')"

if [ -z "$COMMAND" ]; then
  exit 0
fi

block() {
  echo "guard_bash.sh: BLOCKED — $1" 1>&2
  echo "guard_bash.sh: command was: $COMMAND" 1>&2
  exit 2
}

# 1) Catastrophic rm -rf patterns. Match permissively but not so loosely that
#    legitimate cleanup of a known subtree is blocked. The set below is
#    intentionally narrow: we trust local repo cleanup, we do NOT trust wipes
#    of root, scratch, projects, or home.
case "$COMMAND" in
  *"rm -rf /"|*"rm -rf / "*) block "rm -rf / is never legitimate" ;;
  *"rm -rf /scratch"*|*"rm -rf /scratch/"*) block "do not nuke /scratch wholesale; back up metadata.sqlite first" ;;
  *"rm -rf /projects"*|*"rm -rf /projects/"*) block "do not nuke /projects; that is the persistent home" ;;
  *"rm -rf /home"*|*"rm -rf /home/"*|*"rm -rf ~"*|*"rm -rf \$HOME"*) block "do not nuke home directories" ;;
  *"rm -rf \$SCRATCH"*|*"rm -rf \${SCRATCH"*) block "do not nuke \$SCRATCH; back up metadata.sqlite first" ;;
esac

# 2) git push --force to main / master. Force-push to a local feature branch is fine.
if printf '%s' "$COMMAND" | grep -Eq 'git[[:space:]]+push[[:space:]]+(.+[[:space:]]+)?(--force|-f)\b'; then
  if printf '%s' "$COMMAND" | grep -Eq '(^|[[:space:]])(main|master)([[:space:]]|$)'; then
    block "force-push to main/master is never legitimate; open a PR or rebase locally"
  fi
fi

# 3) git commit — scan the staged diff for HuggingFace tokens.
if printf '%s' "$COMMAND" | grep -Eq '(^|[[:space:]])git[[:space:]]+commit\b'; then
  if ! bash "$(dirname "$0")/scan_secrets.sh" --staged; then
    block "scan_secrets.sh detected a HuggingFace token in the staged diff"
  fi
fi

exit 0
