#!/usr/bin/env bash
# Stop hook — launches the approved vault-tidy loop (see vault-tidy-run.sh + the Decision note).
# Deliberately trivial: guard against recursion, then fire the worker DETACHED so session-end
# never blocks on git/network. The worker holds its own lock, fences, and logging.
set -euo pipefail

INPUT="$(cat 2>/dev/null || true)"
# stop_hook_active guards against a Stop that itself triggers another Stop event.
ACTIVE="$(printf '%s' "$INPUT" | jq -r '.stop_hook_active // false' 2>/dev/null || echo false)"
[ "$ACTIVE" = "true" ] && exit 0

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNNER="$SCRIPT_DIR/vault-tidy-run.sh"
[ -x "$RUNNER" ] || [ -f "$RUNNER" ] || exit 0

# Sample health metrics from the LIVE tree (the worker below runs on a clean worktree, so trend
# data must be read here). Detached + best-effort: an append to .vault-health/ never blocks the
# session and never fails the hook.
ENGINE="$SCRIPT_DIR/vault-tidy.py"
VAULT="${VAULT_TIDY_ROOT:-$HOME/obsidian}"
if command -v python3 >/dev/null 2>&1 && [ -f "$ENGINE" ] && [ -d "$VAULT" ]; then
  nohup python3 "$ENGINE" --metrics --root "$VAULT" >/dev/null 2>&1 &
fi

# Detach fully so the terminal returns immediately at session end.
nohup bash "$RUNNER" >/dev/null 2>&1 &

exit 0
