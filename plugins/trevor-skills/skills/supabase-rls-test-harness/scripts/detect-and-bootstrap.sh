#!/usr/bin/env bash
# detect-and-bootstrap.sh — idempotent probe + minimal setup for RLS DB testing.
#
# Many repos have NO db-test infra yet (no supabase/tests, CI never starts
# Supabase). This brings up only what's missing and WARNS about the schema facts a
# generated test must respect. It writes nothing outside supabase/tests/ and never
# touches CI/branch protection (that's Step 6, human-applied). Run from the repo root.
set -euo pipefail

ok()   { printf '  \033[32m✔\033[0m %s\n' "$1"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$1"; }
info() { printf '  \033[36m•\033[0m %s\n' "$1"; }

[[ -d supabase ]] || { echo "ERROR: no ./supabase dir — run this from the repo root." >&2; exit 2; }

echo "=== 1. Supabase CLI ==="
if ! command -v supabase >/dev/null 2>&1; then
  echo "ERROR: supabase CLI not found. Install: brew install supabase/tap/supabase (or 'npm i -g supabase')." >&2
  exit 1
fi
ok "supabase $(supabase --version 2>/dev/null | head -1)"

echo "=== 2. Local stack ==="
if supabase status >/dev/null 2>&1; then
  ok "local stack is up"
else
  info "starting local Postgres (supabase db start; needs Docker)..."
  supabase db start
  ok "stack started"
fi
DB_URL="$(supabase status -o env 2>/dev/null | sed -n 's/^DB_URL="\{0,1\}\([^"]*\)"\{0,1\}/\1/p')"

echo "=== 3. pgTAP + tests dir ==="
mkdir -p supabase/tests/database
ok "supabase/tests/database/ exists"
if [[ -n "${DB_URL}" ]]; then
  psql "${DB_URL}" -v ON_ERROR_STOP=1 -q -c "create extension if not exists pgtap with schema extensions;" \
    && ok "pgtap extension available on local DB" \
    || warn "could not enable pgtap (each test also runs 'create extension if not exists pgtap' as a fallback)"
else
  warn "no DB_URL from 'supabase status'; each test file enables pgtap itself."
fi

echo "=== 4. Schema facts a test MUST respect (grepping migrations) ==="
if grep -rilE 'after insert on auth\.users' supabase/migrations >/dev/null 2>&1; then
  warn "auth.users AFTER INSERT triggers exist (handle_new_user / handle_new_user_org):"
  info "  minting a test user auto-creates a profile + organization + org_members row."
  info "  -> generated tests seed EXPLICIT orgs/memberships with active=true and assert by"
  info "     literal id, so the auto-created rows are harmless noise. Do not depend on them."
fi
if grep -rqiE 'org_members' supabase/migrations 2>/dev/null; then
  info "org_members detected. Reminder: is_org_member() requires active=true; that flag comes"
  info "  from the COLUMN DEFAULT, not the signup trigger — the templates seed active=true explicitly."
fi

echo "=== 5. CI status (report only) ==="
if ls .github/workflows/*.y*ml >/dev/null 2>&1 && grep -rqiE 'supabase test db' .github/workflows 2>/dev/null; then
  ok "a workflow already runs 'supabase test db'"
else
  warn "no CI job runs 'supabase test db' yet."
  info "  Step 6 copies scripts/db-tests.workflow.yml -> .github/workflows/db-tests.yml and prints"
  info "  the branch-protection command. Until that required check is applied, the suite is ADVISORY"
  info "  (auto-merge only waits on REQUIRED checks). See references/ci-wiring.md."
fi

echo "=== bootstrap complete — ready to generate (Step 3). ==="
