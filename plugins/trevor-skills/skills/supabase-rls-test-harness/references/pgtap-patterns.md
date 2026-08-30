# pgTAP assertion playbook for RLS isolation

The generator assembles these blocks; this is the human reference for reading, tweaking, or
hand-writing a test. Every file follows the same skeleton.

## File skeleton
```sql
begin;
create extension if not exists pgtap with schema extensions;
-- ... seed as superuser (BYPASSRLS) ...
select plan(N);                 -- N = number of assertions (generator counts them)
-- ... RLS-enabled check ...
set local role authenticated;   -- lose BYPASSRLS
select set_config('request.jwt.claims', '{"sub":"<alice-uuid>","role":"authenticated"}', true);
-- ... assertions as alice ...
reset role;                     -- back to superuser for ground-truth re-read
select * from finish();
rollback;                       -- residue-free; nothing is committed
```
`begin … rollback` gives transaction isolation: fast, no cleanup, no cross-test bleed.

## Impersonation (the load-bearing mechanic)
- `set local role authenticated;` — RLS now applies (the role is **not** BYPASSRLS).
- `set_config('request.jwt.claims', '{"sub":"…","role":"authenticated"}', true)` — makes
  `auth.uid()` and `auth.jwt()` resolve. Supabase's `auth.uid()` reads `sub` from these
  claims. `true` = transaction-local.
- Always assert the switch worked: `isnt(auth.uid(), null)` + `is(auth.uid(), '…'::uuid)`.

## Assertion → failure-mode map
RLS denies in two different ways; pick the matching assertion or the test silently passes.

| Operation | How RLS denies | Assertion |
|---|---|---|
| `SELECT` / `UPDATE` / `DELETE` filtered by `USING` | rows are **invisible** → 0 affected (no error) | `is_empty('… where tenant = B returning …')` |
| own-row exists | positive control | `isnt_empty('… where tenant = A')` |
| exact visibility | catches leak-all **and** deny-all | `results_eq('select count(*)::int …', array[1], …)` |
| `INSERT` / `UPDATE` violating `WITH CHECK` | **raises** error | `throws_ok(sql, '42501', 'new row violates row-level security policy for table "<t>"', …)` |
| own-row write | positive control | `lives_ok('insert … tenant = A …')` |

Key subtleties:
- A blocked `UPDATE`/`DELETE` is a **silent 0-row no-op**, not an error → use `is_empty(...
  returning ...)`, never `throws_ok`.
- A blocked `INSERT` (or a tenant-hop `UPDATE` that moves a row to another tenant) **throws**
  → use `throws_ok` and **pin the message** (see cardinal-mistakes #5).
- For a policy with `USING` and no explicit `WITH CHECK`, Postgres reuses `USING` as the
  check, so the tenant-hop `UPDATE` still throws the same message. Correct and intended.

## The anti-vacuity triad (every table)
1. **positive control** — A can act on its own row.
2. **negative control** — A sees/affects 0 of B's rows.
3. **exact count** — A sees exactly the expected number.
Plus a **ground-truth re-read as superuser** after the attacks (B's row untouched), and the
**RLS-enabled** flag check. Together, a `DISABLE RLS` or a `USING(true)` leak turns multiple
assertions red — which is exactly what `mutation-check.sh` verifies.

## plan(N)
`plan(N)` must equal the number of assertions, or pgTAP reports a planning failure. The
generator counts emitted assertions and writes `N` — never hand-edit it without recounting.

## storage.objects (advanced, more fragile)
Org-aware bucket policies derive `org_id` from a parent table via
`(storage.foldername(name))[1]::uuid`. Testing them means seeding `storage.objects` rows with
a correctly-encoded `name` path (`<parent-id>/file.ext`) and a parent row in the org. v1
templates do not auto-generate these; hand-write using the same impersonation + triad, seeding
the parent row and the `storage.objects` row as superuser. Treat as a manual extension.
