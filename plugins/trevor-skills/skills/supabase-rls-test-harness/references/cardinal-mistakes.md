# Cardinal mistakes — the false-pass traps an RLS test must avoid

An RLS test is only worth committing if it goes **red when isolation breaks**. The failure
mode that matters is not "the test errors" — it's "the test passes while a tenant can read
another tenant's PHI." Every guard in the templates exists to defeat one of these traps.
`scripts/mutation-check.sh` is the backstop that proves the guards work.

## 1. Asserting as a BYPASSRLS identity (the #1 trap)
`postgres` / `service_role` have **BYPASSRLS** — RLS is not evaluated for them, so every
query "succeeds" and every negative test passes vacuously.
- **Guard:** seed as the superuser, then `set local role authenticated` **before any
  assertion**, and set `request.jwt.claims` so `auth.uid()` resolves. Re-read ground truth
  only after `reset role`.
- **Proof:** `select isnt((select auth.uid()), null, ...)` and `select is(auth.uid(),
  '<alice>'::uuid, ...)` immediately after the role switch.

## 2. `auth.uid()` is NULL → negatives pass for the wrong reason
If claims aren't set, `auth.uid()` is NULL, every org check is false, and the negative
("can't see B") passes — but so would a totally broken policy.
- **Guard:** the two `auth.uid()` assertions above. If they fail, the impersonation is
  wrong and nothing below can be trusted.

## 3. All-negative suites pass by blanket denial
A suite that only checks "alice sees 0 of B's rows" passes if the policy denies
*everything*, including alice's own rows.
- **Guard:** a **positive control** (`isnt_empty` for A's own row, `lives_ok` for A's own
  INSERT) **and** an **exact count** (`results_eq(..., array[1])`) so both a deny-all and a
  leak-all policy fail.

## 4. Reading a setup-owned table from the authenticated role (subtle, was a real bug)
A temp/working table created during the superuser seed is **not granted to
`authenticated`**. Selecting it after the role switch raises `permission denied ... (42501)`
**before RLS is evaluated** — which (a) makes a correct policy look broken and (b) makes a
`throws_ok(..., '42501')` write-negative **pass on the wrong error**.
- **Guard:** the generator inlines **literal UUIDs**; no assertion reads a setup-owned
  object. (If you ever must, `grant select on <t> to authenticated;` before switching.)

## 5. Matching only SQLSTATE `42501` on a write-negative
`42501` is generic `insufficient_privilege` — raised by an RLS `WITH CHECK` violation **and**
by ordinary missing-GRANT/permission errors. Asserting the code alone certifies isolation
you never exercised.
- **Guard:** `throws_ok` asserts the **exact message**
  `new row violates row-level security policy for table "<t>"` (or a `like` on
  `violates row-level security policy`).

## 6. The `WITH CHECK` gap on writes
A `FOR INSERT`/`FOR UPDATE` policy with `WITH CHECK (true)` (or a `FOR ALL` `USING` with no
`WITH CHECK` where Postgres can't fall back) lets a tenant **write** a forged-tenant row even
though `SELECT` is locked down. Read-only isolation tests miss this entirely.
- **Guard:** the INSERT-forge `throws_ok` (insert a row in org B as alice) and the
  UPDATE tenant-hop `throws_ok` (set `org_id = B` on alice's own row). Note: for a policy
  with `USING` and **no** explicit `WITH CHECK`, Postgres reuses `USING` as the check, so the
  tenant-hop still throws — that's correct and intended, not a gap.

## 7. A green check that enforces nothing (fix this in CI, not SQL)
Even a perfect suite is not a "floor" if its CI job is **not a required status check**:
`gh pr merge --auto --squash` only waits on **required** checks, so a red `db-tests` job
does not block the merge. **The regression floor does not exist until branch protection
names `db-tests` as required.** See `ci-wiring.md`. Until then, call it *advisory*.

## Why `mutation-check.sh` is the differentiator
A reviewer can *say* a policy looks right. Only a test that has been **shown to fail when the
policy is broken** proves it. The mutation check disables RLS (flag-level leak) and adds a
permissive `USING (true)` policy (data-level leak with RLS still on) and requires the suite
to go red both times. If it stays green, the test is vacuous — fix it here, do not weaken it.
