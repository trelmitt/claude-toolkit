#!/usr/bin/env bash
# SessionStart hook — config-drift watch (the self-healing backstop).
# Warns when any of the portable-config repos have uncommitted changes or
# unpushed commits, so local work can't silently fail to reach other machines.
# Cheap, OFFLINE (no fetch), read-only, never blocks. Skips any repo not present.
set -euo pipefail

# Repos that are supposed to stay synced across machines. Skipped silently if absent,
# and any entry without a .git dir is skipped too (e.g. an Obsidian-only vault mirror).
REPOS=(
  # (Route A / ~/.claude/skills retired 2026-07-15 — skills live in the marketplace below now;
  #  doctor.sh's Route-A-leftovers check owns any lingering husk, so watching it here is redundant.)
  "${HOME}/claude-config"                    # global CLAUDE.md + settings (constitution)
  "${HOME}/claude-dev-loop-marketplace"      # dev-loop + trevor-skills plugins (Route B)
  "${CLAUDE_VAULT_DIR:-$HOME/obsidian}"  # PARA vault (git-backed mirror; skipped if Obsidian-only)
  "${CRACKED_MARKET_DIR:-$HOME/cracked-dev-marketplace}"  # cracked-tools marketplace (cracked-dev; honors $CRACKED_MARKET_DIR, like sync-cracked-dev.sh)
)

drift=""
for r in "${REPOS[@]}"; do
  # probe with git (not [ -d "$r/.git" ]) so linked worktrees, whose .git is a file, still count
  git -C "$r" rev-parse --is-inside-work-tree >/dev/null 2>&1 || continue
  name="$(basename "$r")"

  # Uncommitted / untracked changes.
  if [ -n "$(git -C "$r" status --porcelain 2>/dev/null)" ]; then
    drift+="  • ${name}: uncommitted changes\n"
  fi

  # Unpushed commits (offline — compares against last-known upstream ref).
  up="$(git -C "$r" rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)"
  if [ -n "$up" ]; then
    # Establish the upstream ref actually resolves BEFORE counting. If a branch has an upstream
    # configured but its refs/remotes/<up> is absent locally (after `git remote prune`, a
    # --single-branch/shallow clone, or a deleted tracking ref), `rev-list --count @{u}..HEAD`
    # exits 128 and `|| echo 0` swallows that into ahead=0 — a false "everything pushed" while real
    # unpushed commits sit local. Surface the unresolvable ref instead of reporting clean.
    if git -C "$r" rev-parse --verify -q '@{u}' >/dev/null 2>&1; then
      ahead="$(git -C "$r" rev-list --count '@{u}..HEAD' 2>/dev/null || echo 0)"
      if [ "${ahead:-0}" -gt 0 ]; then
        drift+="  • ${name}: ${ahead} unpushed commit(s)\n"
      fi
    else
      drift+="  • ${name}: upstream '${up}' set but its tracking ref is missing — can't verify unpushed commits (run: git -C ${r} fetch)\n"
    fi
  fi
done

if [ -n "$drift" ]; then
  printf '[dev-loop] ⚠ config drift — local changes not yet propagated to other machines:\n'
  printf "%b" "$drift"
  printf '          Fix: run ~/.claude/sync-skills.sh (skills) or commit+push the repo above.\n'
fi
exit 0
