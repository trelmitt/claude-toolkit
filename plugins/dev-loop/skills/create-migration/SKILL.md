---
name: create-migration
description: Scaffold a new Supabase migration in supabase/migrations with RLS enabled and a starter policy. Use when adding or changing database schema. Enforces the CLAUDE.md rule that every new table has RLS.
disable-model-invocation: true
---

# Create Supabase Migration

Scaffold a timestamped migration file with RLS baked in so the safe path is the default path.

## Steps

1. **Confirm intent.** Take the table/change name from the user's args (e.g. `/create-migration profiles`). If none given, ask what the migration is for.

2. **Generate a UTC timestamp filename.** Format: `supabase/migrations/<YYYYMMDDHHMMSS>_<short_snake_case_desc>.sql`. Get the timestamp with:
   ```bash
   date -u +%Y%m%d%H%M%S
   ```
   Verify `supabase/migrations/` exists; if not, tell the user this doesn't look like the Supabase project root and stop.

3. **Write the migration.** For a NEW TABLE, use this template (substitute the real table name and columns). RLS is non-negotiable per CLAUDE.md:
   ```sql
   -- <description>
   create table if not exists public.<table> (
     id uuid primary key default gen_random_uuid(),
     user_id uuid not null references auth.users (id) on delete cascade,
     created_at timestamptz not null default now()
     -- add columns here
   );

   -- Enable Row Level Security (required on every table).
   alter table public.<table> enable row level security;

   -- Owner-scoped policies. Tighten/loosen per the actual access model.
   create policy "<table>_select_own" on public.<table>
     for select using (auth.uid() = user_id);
   create policy "<table>_insert_own" on public.<table>
     for insert with check (auth.uid() = user_id);
   create policy "<table>_update_own" on public.<table>
     for update using (auth.uid() = user_id) with check (auth.uid() = user_id);
   create policy "<table>_delete_own" on public.<table>
     for delete using (auth.uid() = user_id);
   ```
   For schema changes to an EXISTING table, write the `alter table` statements and do NOT re-enable RLS, but remind the user to add policies if the change exposes new data.

4. **Report** the created path and a one-line summary. Remind the user to review policies, then run `npx supabase db push` (or apply via migration in CI) — never edit production SQL directly.

## Rules
- Never put schema changes anywhere but `supabase/migrations/`.
- Never create a table without `enable row level security` + at least one policy.
- Default policies to owner-scoped (`auth.uid() = user_id`); only widen deliberately.
