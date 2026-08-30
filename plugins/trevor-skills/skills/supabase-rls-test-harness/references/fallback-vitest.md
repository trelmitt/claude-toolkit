# Fallback: two-client Vitest integration harness

Use this **only** when pgTAP doesn't fit — the team won't maintain SQL, or the policy depends
on Auth/API behavior only the running stack exposes. pgTAP is preferred (rollback isolation is
faster and cleaner, and the repo's policies are pure DB logic). This reuses the repo's
existing Vitest (`bun run test`) — no new test runner.

## Shape
Two kinds of client:
- a **service_role** (SECRET) client — BYPASSRLS — used **only** to seed tenants and mint
  users via `auth.admin.createUser`. Never used in an assertion.
- a **fresh anon (PUBLISHABLE) client per user** (`persistSession:false`,
  `autoRefreshToken:false`), signed in as that user, used for the isolation assertions.

```ts
import { createClient } from "@supabase/supabase-js";
const URL = process.env.SUPABASE_URL!;
// HARD GUARD: never run against prod / real PHI.
if (!/^https?:\/\/(127\.0\.0\.1|localhost)/.test(URL)) throw new Error("RLS tests must hit the LOCAL stack");

const admin = createClient(URL, process.env.SUPABASE_SECRET_KEY!, { auth: { persistSession: false } });
function userClient() { return createClient(URL, process.env.SUPABASE_PUBLISHABLE_KEY!, { auth: { persistSession: false, autoRefreshToken: false } }); }
```

## Three guardrails over the naive example
1. **Local-URL guard** (above) — a swapped env must throw, never touch prod PHI.
2. **Control test**: the SECRET client sees **both** tenants while a brand-new anon client
   (no session) sees `[]`. Catches a leaked/swapped key and globally-disabled RLS.
3. **Asymmetric write checks**: a blocked cross-tenant `UPDATE`/`DELETE` is a **silent no-op**
   (`data: []`, `error: null`) → verify by re-reading the row as its owner; a blocked
   cross-tenant `INSERT` returns a **403** `new row violates row-level security policy`.

## Pitfalls
- No transaction rollback here → use `crypto.randomUUID()` ids, clean up in `afterAll`, and
  run with `--no-file-parallelism` to avoid session bleed across files.
- Key-name drift: newer Supabase uses PUBLISHABLE/SECRET; older uses ANON/SERVICE_ROLE. Pull
  them from the local stack only:
  `supabase status -o env --override-name auth.anon_key=SUPABASE_PUBLISHABLE_KEY --override-name auth.service_role_key=SUPABASE_SECRET_KEY --override-name api.url=SUPABASE_URL >> "$GITHUB_ENV"`
- Same CI gate caveat as pgTAP: the job must be a **required** check or it enforces nothing
  (see `ci-wiring.md`).

## When to pick which
| Prefer pgTAP | Prefer Vitest fallback |
|---|---|
| Pure DB policy (USING/WITH CHECK on a table) | Policy depends on the API/GoTrue layer |
| Want fast, residue-free rollback isolation | Team already lives in TS, won't touch SQL |
| Repo's RLS keys off `auth.uid()` only (it does) | Need to exercise real sign-in / JWT minting |
