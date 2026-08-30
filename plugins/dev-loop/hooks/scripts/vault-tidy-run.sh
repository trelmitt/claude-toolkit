#!/usr/bin/env bash
# vault-tidy worker — the approved end-of-session vault hygiene loop.
# Approved for ONE specific loop in [[Decision - Human-in-the-loop autonomy]] (2026-07-16):
# it OPENS A PR and stops — it never pushes a shared branch. The PR is the human checkpoint.
#
# Safety model (this script is self-contained; the dev-loop PreToolUse floor hooks do NOT gate
# a detached background process, and the vault has no tracked CLAUDE.md, so block-main-push does
# not cover it — every fence below is enforced here):
#   • Works in a THROWAWAY git worktree built from origin/<default>, so the live vault working
#     tree (chronically dirty: Obsidian Sync + concurrent agents) is NEVER touched.
#   • Stages ONLY the files the engine reports changing — never `git add -A`; aborts if anything
#     else is staged.
#   • Pushes ONLY its own vault-tidy/* branch; never a protected branch; never --force.
#   • One open vault-tidy PR at a time (won't stack); one run at a time (lockfile).
#   • Aborts on a mid-merge/rebase tree. Every failure logs and exits 0 (never traps a session).
#
# Usage: vault-tidy-run.sh [--dry-run] [--root <vault-dir>]
set -euo pipefail

DRYRUN=""
VAULT="${VAULT_TIDY_ROOT:-$HOME/obsidian}"
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRYRUN=1 ;;
    --root) shift; VAULT="$1" ;;
    *) ;;
  esac
  shift
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENGINE="$SCRIPT_DIR/vault-tidy.py"

command -v git >/dev/null 2>&1 || exit 0
command -v python3 >/dev/null 2>&1 || exit 0
git -C "$VAULT" rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0
[ -f "$ENGINE" ] || exit 0

GITDIR="$(git -C "$VAULT" rev-parse --absolute-git-dir 2>/dev/null || echo '')"
[ -n "$GITDIR" ] || exit 0
LOG="$GITDIR/vault-tidy.log"

log() { printf '[vault-tidy %s] %s\n' "$(date '+%Y-%m-%dT%H:%M:%S')" "$*" >>"$LOG"; }

# --- one run at a time (mkdir is atomic). Self-healing: a lock older than any real run could
#     take is stale (a prior run SIGKILLed / power-lost before its EXIT trap fired) — reclaim it,
#     else an untrappable kill would disable the loop forever. flock(1) is absent on macOS. ---
LOCK="$GITDIR/vault-tidy.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  if [ -n "$(find "$LOCK" -maxdepth 0 -mmin +30 2>/dev/null)" ] \
       && rmdir "$LOCK" 2>/dev/null && mkdir "$LOCK" 2>/dev/null; then
    log "reclaimed a stale lock (>30m old)"
  else
    log "another run holds the lock; exit"; exit 0
  fi
fi

WT=""
cleanup() {
  [ -n "$WT" ] && git -C "$VAULT" worktree remove --force "$WT" >/dev/null 2>&1 || true
  [ -n "$WT" ] && rm -rf "$WT" 2>/dev/null || true
  # the branch ref lives in the SHARED repo (not the worktree), so delete it after the worktree
  [ -n "${BRANCH:-}" ] && git -C "$VAULT" branch -D "$BRANCH" >/dev/null 2>&1 || true
  rmdir "$LOCK" 2>/dev/null || true
}
trap cleanup EXIT

# reap worktree admin entries orphaned by a prior SIGKILL/reboot (cleanup can't self-heal those)
git -C "$VAULT" worktree prune >/dev/null 2>&1 || true

log "=== run start (dry-run=${DRYRUN:-0}) vault=$VAULT ==="

# --- abort on a mid-operation tree ---
if [ -f "$GITDIR/MERGE_HEAD" ] || [ -d "$GITDIR/rebase-merge" ] || [ -d "$GITDIR/rebase-apply" ]; then
  log "vault is mid-merge/rebase; skipping"; exit 0
fi

# --- default branch (via origin/HEAD; fall back to main) ---
DEF="$(git -C "$VAULT" symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's@^origin/@@' || true)"
[ -n "$DEF" ] || DEF="main"

# --- don't stack PRs: skip if an open vault-tidy PR already exists. FAIL CLOSED — a gh error
#     must not read as "no open PR" (that would stack a second PR). ---
if [ -z "$DRYRUN" ] && command -v gh >/dev/null 2>&1; then
  OPEN_PRS="$(cd "$VAULT" && gh pr list --state open --json headRefName --jq '.[].headRefName' 2>>"$LOG")" \
    || { log "gh pr list failed; skip (cannot verify no open PR)"; exit 0; }
  if printf '%s\n' "$OPEN_PRS" | grep -q '^vault-tidy/'; then
    log "an open vault-tidy PR already exists; skip (let the human merge it first)"; exit 0
  fi
fi

# --- fetch + isolated worktree from the CLEAN remote tip (never the dirty live tree) ---
git -C "$VAULT" fetch origin "$DEF" --quiet 2>>"$LOG" || { log "fetch failed; skip"; exit 0; }
WT="$(mktemp -d "${TMPDIR:-/tmp}/vault-tidy.XXXXXX")"
# (no WT="" on failure: cleanup's rm -rf then reclaims the mktemp dir mktemp -d already created)
git -C "$VAULT" worktree add --detach "$WT" "origin/$DEF" --quiet 2>>"$LOG" || { log "worktree add failed; skip"; exit 0; }

# --- run the deterministic engine; capture ONLY the files it changed ---
CHANGED="$(python3 "$ENGINE" --apply --root "$WT" 2>>"$LOG" || true)"
if [ -z "$CHANGED" ]; then log "no mechanical fixes; nothing to PR"; exit 0; fi
log "engine changed:"; printf '%s\n' "$CHANGED" >>"$LOG"

# Deterministic branch from base commit + a hash of the changed-file set: two clones building the
# same fixes collide on ONE branch and git's non-fast-forward push rejection drops the loser
# (the atomicity the gh-pr-list check lacks); a re-run with identical fixes is a no-op push.
BRANCH="vault-tidy/$(git -C "$WT" rev-parse --short HEAD)-$(printf '%s\n' "$CHANGED" | git -C "$WT" hash-object --stdin | cut -c1-8)"
git -C "$WT" checkout -b "$BRANCH" --quiet

# stage ONLY the changed files, one by one (never `git add -A`)
while IFS= read -r f; do
  [ -n "$f" ] && git -C "$WT" add -- "$f"
done <<EOF
$CHANGED
EOF

# fence: refuse if anything other than the engine's files got staged.
# core.quotePath=false so git emits raw UTF-8 paths (default C-quotes non-ASCII names, which
# the engine does not — that mismatch would false-positive this fence and abort valid tidies).
EXTRA="$(git -C "$WT" -c core.quotePath=false diff --cached --name-only | grep -vxF -f <(printf '%s\n' "$CHANGED") || true)"
if [ -n "$EXTRA" ]; then log "unexpected staged files, aborting: $EXTRA"; exit 0; fi

git -C "$WT" -c user.name="vault-tidy" -c user.email="vault-tidy@localhost" \
  commit -m "chore(vault): mechanical tidy — wikilinks + frontmatter" --quiet

# --- PR body: fixes + report-only context (never auto-touched) ---
REPORT_JSON="$(python3 "$ENGINE" --report --json --root "$WT" 2>/dev/null || echo '{}')"
# %-formatting (not f-strings): system python3 is 3.9.6, where an escaped-quote f-string expression
# is a SyntaxError — which would silently drop the whole report-only context from the PR body.
BODY="$(printf '%s' "$REPORT_JSON" | python3 -c '
import json, sys
try:
    f = json.load(sys.stdin)
except Exception:
    f = {}
u = len(f.get("unresolvable_links", []))
a = len(f.get("ambiguous_links", []))
o = len(f.get("orphans", []))
m = len(f.get("frontmatter_missing", []))
print("Deterministic mechanical vault tidy (scope: wikilinks + frontmatter only).")
print("Approved loop: Decision - Human-in-the-loop autonomy (2026-07-16). The PR is the checkpoint.")
print("")
print("**Report-only (NOT touched — needs judgment):**")
print("- %d unresolvable links (forward-links to notes/skills that do not exist)" % u)
print("- %d ambiguous links" % a)
print("- %d orphan notes" % o)
print("- %d notes missing a type/surface value" % m)
' 2>/dev/null || echo 'Mechanical vault tidy.')"

if [ -n "$DRYRUN" ]; then
  log "DRY-RUN: would push $BRANCH -> open PR against $DEF"
  git -C "$WT" --no-pager show --stat HEAD >>"$LOG" 2>&1 || true
  exit 0
fi

# --- push ONLY this branch, never force ---
git -C "$WT" push origin "refs/heads/$BRANCH:refs/heads/$BRANCH" --quiet 2>>"$LOG" \
  || { log "push failed; skip PR"; exit 0; }

if command -v gh >/dev/null 2>&1; then
  if (cd "$WT" && gh pr create --base "$DEF" --head "$BRANCH" \
        --title "chore(vault): mechanical tidy — wikilinks + frontmatter" \
        --body "$BODY" >>"$LOG" 2>&1); then
    log "PR opened for $BRANCH"
  else
    # remove the just-pushed branch so it doesn't orphan on the remote; the tidy is idempotent
    # and re-runs next session
    log "gh pr create failed; removing the orphan branch $BRANCH (tidy retries next run)"
    git -C "$WT" push origin --delete "$BRANCH" >/dev/null 2>&1 || true
  fi
else
  log "gh not available; branch $BRANCH pushed, open the PR manually"
fi
exit 0
