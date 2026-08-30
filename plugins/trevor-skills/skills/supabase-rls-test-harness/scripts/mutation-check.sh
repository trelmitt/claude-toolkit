#!/usr/bin/env bash
# mutation-check.sh — prove a generated RLS test actually has TEETH.
#
# A test that passes is worthless if it would ALSO pass when isolation is broken.
# This script breaks isolation two independent ways and asserts the suite goes RED:
#   1. DISABLE ROW LEVEL SECURITY        -> the RLS-enabled flag check + leak asserts go red
#   2. add a permissive `USING (true)`   -> RLS stays ON but data leaks; proves the DATA
#      SELECT policy                         asserts (exact-count / is_empty) catch a subtle
#                                            broadened predicate, independent of the flag
# It restores the database with `supabase db reset` at the end and on any interrupt.
#
# Usage:  scripts/mutation-check.sh <table> [schema=public]
# Assumes: the local stack is up (run detect-and-bootstrap.sh first) and the test suite is
#          GREEN at baseline. Run from the repo root.
set -euo pipefail

TABLE="${1:?usage: mutation-check.sh <table> [schema=public]}"
SCHEMA="${2:-public}"
QUALIFIED="${SCHEMA}.${TABLE}"
LEAK_POLICY="zzz_mutation_check_leak"

DB_URL="$(supabase status -o env 2>/dev/null | sed -n 's/^DB_URL="\{0,1\}\([^"]*\)"\{0,1\}/\1/p')"
if [[ -z "${DB_URL}" ]]; then
  echo "ERROR: could not read DB_URL from 'supabase status'. Is the local stack up? Run detect-and-bootstrap.sh." >&2
  exit 2
fi
psql_do() { psql "${DB_URL}" -v ON_ERROR_STOP=1 -q -c "$1"; }

restore() {
  echo "--- restoring database (supabase db reset) ---"
  psql "${DB_URL}" -q -c "drop policy if exists ${LEAK_POLICY} on ${QUALIFIED};" >/dev/null 2>&1 || true
  psql "${DB_URL}" -q -c "alter table ${QUALIFIED} enable row level security;" >/dev/null 2>&1 || true
  supabase db reset >/dev/null 2>&1 || echo "WARN: 'supabase db reset' failed — restore the local DB manually." >&2
}
trap restore EXIT

run_suite() { supabase test db >/tmp/rls-mc.out 2>&1; }  # exit 0 = all passed

echo "=== mutation-check: ${QUALIFIED} ==="

echo "[baseline] suite must be GREEN before we can prove teeth..."
if ! run_suite; then
  echo "ERROR: baseline 'supabase test db' is RED. Fix the test/fixtures first (Step 4), then re-run." >&2
  sed -n '1,40p' /tmp/rls-mc.out >&2
  exit 1
fi
echo "[baseline] GREEN."

fail_expected() { # $1 = mutation label
  if run_suite; then
    echo "VACUOUS: suite STILL PASSED after [$1]." >&2
    echo "  Either the test asserts as a BYPASSRLS identity / lacks a real negative+positive control," >&2
    echo "  or your 'supabase test db' resets the DB before running (re-applying the mutation away)." >&2
    echo "  Inspect the test against references/cardinal-mistakes.md before trusting it." >&2
    exit 1
  fi
  echo "[$1] suite correctly went RED. ✔"
}

echo "[mutation 1] disabling RLS on ${QUALIFIED}..."
psql_do "alter table ${QUALIFIED} disable row level security;"
fail_expected "disable RLS"
psql_do "alter table ${QUALIFIED} enable row level security;"

echo "[mutation 2] adding permissive USING(true) SELECT policy (RLS stays ON)..."
psql_do "create policy ${LEAK_POLICY} on ${QUALIFIED} for select to authenticated using (true);"
fail_expected "permissive USING(true) leak"
psql_do "drop policy ${LEAK_POLICY} on ${QUALIFIED};"

echo "=== PASS: the test has teeth — it fails when isolation breaks both ways. ==="
# restore() runs on EXIT
