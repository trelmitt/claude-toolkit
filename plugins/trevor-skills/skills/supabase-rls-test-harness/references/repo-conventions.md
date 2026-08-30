# Repo conventions the generator must honor (template)

The generator and templates are built around a handful of facts about the repo under test.
These vary per project, so **detect them each run** (parse the SQL and config — schemas drift)
and fill in the shape below. The example names are placeholders; substitute your own.

## The two RLS shapes
1. **Org/tenant-scoped** (most tables — e.g. `items`, `documents`, `notes`, …). Policies key
   off the tenant via one of:
   - a `SECURITY DEFINER STABLE` helper like `is_org_member(auth.uid(), org_id)` — the common
     form; note whether it **requires an `active = true`** (or similar) membership flag.
   - inline `org_id IN (select org_id from public.org_members where user_id = auth.uid())`
     — a variant that often **omits** the `active` check.
   → use the **org-isolation** template; default tenant column `org_id`.
2. **Owner-scoped** (e.g. `profiles`, `push_subscriptions`): `auth.uid() = id`.
   → use the **owner-scoped** template; when the owner column **is** the PK (e.g. `profiles`),
   pass `--owner-col id --owner-is-pk`.

## Membership + an `active` flag (a common gotcha)
If a helper like `is_org_member()` requires `active = true`, check where that `true` comes
from. It is often a **COLUMN DEFAULT** on `org_members.active`, **not** the signup trigger — a
`handle_new_user_org`-style trigger may insert `(org_id, user_id, role)` only and never set
`active`. So:
- Templates **seed `active = true` explicitly** (never rely on the trigger or the default). If
  a future migration drops that default, explicit seeding keeps tests valid.
- An inline variant that omits the `active` check would let an **inactive** member pass — that
  semantic inconsistency is a finding for a security reviewer, **not** this skill. This skill
  tests each policy *as written*.

## Signup triggers (side effects when minting a user)
Look for `AFTER INSERT ON auth.users` triggers (e.g. one that creates a `profiles` row, one
that creates an `organizations` row + an owner `org_members` row). If present, inserting a test
user **auto-creates** those rows. So:
- Org tests seed **explicit** orgs/memberships with **literal ids** and assert by those ids;
  the trigger's auto-created rows are harmless noise.
- For an owner test on the trigger-created row (`--owner-is-pk`), the generator omits the manual
  seed and uses the user id as the row id. Read back / rely on the trigger row rather than
  inserting a duplicate.

## Migrations & tooling — detect per repo
- `supabase/migrations/` — filenames may be opaque (`YYYYMMDDHHmmss_<uuid>.sql`). **Parse the
  SQL (`CREATE POLICY … USING/WITH CHECK`), never the filename**, to learn intent.
- Detect the **package manager** (npm / pnpm / bun) from the lockfile; use whatever CI uses.
- Check `config.toml` and whether a `seed.sql` exists — if there is no seed, `supabase db start`
  applies migrations and that's the full fixture base.
- Detect the **default branch** (don't assume `main`); base PRs on it and follow the project's
  own `CLAUDE.md` dev-loop. This skill **complements** a security reviewer on every Supabase
  diff — it does not replace it.
