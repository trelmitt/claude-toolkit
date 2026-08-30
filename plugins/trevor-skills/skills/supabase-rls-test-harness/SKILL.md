---
name: supabase-rls-test-harness
description: >
  GENERATES and commits executable tests that PROVE per-org/tenant row isolation on Supabase —
  tenant A cannot SELECT / INSERT / UPDATE / DELETE tenant B's rows, with positive controls —
  and wires them into CI as a regression floor. It EMITS proof (real .sql pgTAP test files that
  go RED when a policy is removed), it does not just review. Use this whenever someone adds or
  changes an RLS policy, a multi-tenant/org-scoped or owner-scoped table, or a Supabase
  migration, or asks any of: "test my RLS", "write RLS tests", "prove tenant/org isolation",
  "can org A see org B's rows", "lock this policy with a test", "add a regression test for org
  isolation", "pgTAP test for this table", "does this leak across tenants", "verify row-level
  security". Default path is pgTAP via `supabase test db`; falls back to a two-client Vitest
  harness. This is the verification floor the flywheel demands before autonomous loops run on a
  PHI codebase. Distinct from supabase-security-reviewer (which only flags risk, no committed
  artifact) and a future migration-safety-preflight (reliability, not authorization).
---

# Supabase RLS Test Harness

You generate **executable proof of row-level isolation** and wire it into CI. On a PHI EMR,
a tenant reading another tenant's rows is the worst-case failure; a green test suite that
would *also* stay green if isolation broke is worse than no test at all. So the bar is not
"a test exists" — it's "a test that has been **shown to fail when the policy is broken**."

**What you emit:** committed `.sql` pgTAP test files under `supabase/tests/database/`, a CI
job that runs them, and a printed branch-protection command that turns the job into a real
gate. **What you never do:** edit a policy, judge code style, or rate severity — that's the
security reviewer's job.

## When to use / when not
- **Use** when an RLS policy / multi-tenant table / migration is added or changed and you
  want a regression test that locks isolation. Natural hand-off: `supabase-security-reviewer`
  flags a risky policy → this skill writes the test that locks the fix.
- **Not** a reviewer (`supabase-security-reviewer` reads a diff and reports risk — still
  mandated on every Supabase diff; this complements it).
- **Not** a reliability gate (a future `migration-safety-preflight` covers destructive ops,
  locks, downtime). This tests **authorization only** — who can read/write which rows.
- Does not test business logic, edge functions, auth flows, or performance.

## The workflow

### Step 0 — Context intake (confirm scope before generating)
Ask / confirm, presenting interpretations rather than guessing:
- Which **table(s)** or migration file.
- The **shape**: org/tenant-scoped (`is_org_member(auth.uid(), org_id)` or the inline
  `org_members` subquery) → org template; or owner-scoped (`auth.uid() = id`) → owner
  template. See `references/repo-conventions.md`.
- The **tenant column** (default `org_id`) or **owner column** (default `id`; pass
  `--owner-is-pk` when it's the PK referencing `auth.users`, e.g. `profiles`).
- Which **commands** have policies (default all four).
- State plainly: fixtures are **synthetic/anonymized** — these tables hold PHI, so never
  seed from real rows.

### Step 1 — Detect & bootstrap
`bash scripts/detect-and-bootstrap.sh` (from the repo root): checks the Supabase CLI, brings
up the local stack, creates `supabase/tests/database/`, enables pgTAP, and **warns** about
the `auth.users` auto-org triggers and the `active = true` requirement. Idempotent.

### Step 2 — Read the policy as written
Parse the target `CREATE POLICY … USING / WITH CHECK` from the **migration SQL** (filenames
are opaque UUIDs — never infer from them). Note the tenant expression, which commands have
policies, and whether each has an explicit `WITH CHECK`. Surface gaps (e.g. `WITH CHECK
(true)`, or an INSERT with no check) but **do not edit the policy**.

### Step 3 — Generate the test
```bash
# org/tenant-scoped:
bun scripts/gen-rls-test.ts --table documents --kind org --tenant-col org_id
# owner-scoped (PK == auth.users id):
bun scripts/gen-rls-test.ts --table profiles --kind owner --owner-col id --owner-is-pk
```
Renders `supabase/tests/database/NNN-<table>-rls.test.sql` from the matching template with
literal synthetic UUIDs, the requested command blocks, best-effort column introspection, and
an auto-counted `plan(N)`. If it warns about NOT-NULL columns it couldn't infer (emitted as
`null /* TODO */`), fill those in — they're table-specific.

### Step 4 — Run & iterate
`supabase test db`. On failure, **fix the fixture, never weaken an assertion** (common
causes: a missing NOT NULL column, or relying on the trigger for `active`). Keep `plan(N)`
equal to the assertion count (the generator does this; preserve it by hand-editing carefully).

### Step 5 — Mutation check (prove it has teeth) — REQUIRED
`bash scripts/mutation-check.sh <table>`. It requires the suite is green, then breaks
isolation two ways — `DISABLE ROW LEVEL SECURITY` and a permissive `USING (true)` policy —
and asserts the suite goes **red** each time, restoring with `supabase db reset`. If it stays
green, the test is **vacuous** (see `references/cardinal-mistakes.md`) — fix it, don't ship it.
This is the step that separates real proof from theater.

### Step 6 — Wire CI as a regression floor (mind the auto-merge trap)
Copy `scripts/db-tests.workflow.yml` → `.github/workflows/db-tests.yml` (additive; does not
touch `ci.yml`). **Then tell the user, prominently:** the suite is **advisory until branch
protection makes `db-tests` a required check** — `gh pr merge --auto --squash` only waits on
*required* checks, so until then a regression can auto-merge. Print the `gh api …
branches/<default-branch>/protection` command for the **human** to run (shared-branch
protection is outside the autonomous write floor), then verify with `gh pr checks` that the
context is exactly `db-tests`. Full detail + the JSON in `references/ci-wiring.md`.

### Step 7 — Dev-loop hand-off
Stage the test file(s) + workflow on a `feat/` branch and follow the project `CLAUDE.md`
sequence (e.g. code review, type-check, PR `--base <default-branch>`). State
explicitly that this **complements** running `supabase-security-reviewer` on the migration —
it does not replace it.

### Step 8 — Capture what the mutation check taught you
Step 5 makes a test fail on purpose. When a test you *expected* to go red stayed green, you
found a blind spot in how the policy was read — that is the highest-value output of this whole
skill and it is lost the moment the PR merges. Capture it to your knowledge vault (via **`vault-companion`** if you have it): the policy
shape, the assertion that failed to catch it, and the fix.

Capture only genuine surprises — a routine green→red→green cycle needs no note.

## Cardinal-mistake checklist (inline quick guard — full detail in references/cardinal-mistakes.md)
- Never assert as `postgres`/`service_role` (BYPASSRLS) — switch to `authenticated` first.
- Prove the switch: `isnt(auth.uid(), null)` + `is(auth.uid(), '<alice>')` before asserting.
- Always include a **positive control** + an **exact count**, not only negatives.
- `throws_ok` pins the **exact** RLS message, not just SQLSTATE `42501`.
- Cover writes: forged-tenant INSERT and tenant-hop UPDATE (the `WITH CHECK` gap).
- A red CI job that isn't a **required** check enforces nothing.

## Primary (pgTAP) vs fallback (Vitest)
Default to pgTAP — rollback isolation is fast/clean and the repo's policies are pure DB
logic. Use the two-client Vitest harness only when the team won't maintain SQL or the policy
depends on API/Auth behavior. Decision table + skeleton in `references/fallback-vitest.md`.

## Repo quick-reference (example)
A worked example of the facts to detect: an `is_org_member()` helper that is `SECURITY DEFINER
STABLE` and needs `active = true` (from a **column default**, not the signup trigger — seed it
explicitly); `auth.users` `AFTER INSERT` triggers that auto-create profile+org+membership.
Detect your own repo's equivalents each run (package manager, default branch, schema);
`references/repo-conventions.md` is the template to fill in.

## Files
- `scripts/detect-and-bootstrap.sh` — env probe + minimal local setup (Step 1).
- `scripts/gen-rls-test.ts` — the deterministic generator, run with **bun** (Step 3).
- `scripts/templates/org-isolation.sql.tmpl`, `owner-scoped.sql.tmpl` — the two pgTAP shapes.
- `scripts/mutation-check.sh` — proves the test has teeth (Step 5).
- `scripts/db-tests.workflow.yml` — the CI job (Step 6).
- `references/pgtap-patterns.md` — assertion ↔ failure-mode playbook.
- `references/cardinal-mistakes.md` — the false-pass traps and their guards.
- `references/repo-conventions.md` — a template for the schema facts the generator honors.
- `references/ci-wiring.md` — advisory vs required check; branch protection.
- `references/fallback-vitest.md` — the two-client integration fallback.

## Scope boundary
Emits proof + a CI floor; the others advise. `supabase-security-reviewer` returns a
severity-rated risk report (advisory, no committed artifact). A future
`migration-safety-preflight` covers reliability/availability (destructive ops, locks). This
skill makes no claim about migration safety or code quality — only tenant/row isolation
correctness across SELECT/INSERT/UPDATE/DELETE.
