---
name: deploy-edge-function
description: Deploy a Supabase edge function the project-approved way (npx supabase functions deploy <name>) with pre-flight checks. Use when shipping or updating an edge function.
disable-model-invocation: true
---

# Deploy Supabase Edge Function

Wrap the mandated deploy command (`npx supabase functions deploy <name>`) with checks so deploys are consistent and safe.

## Steps

1. **Resolve the function name.** Take it from the user's args (e.g. `/deploy-edge-function stripe-webhook`). If omitted, list candidates and ask:
   ```bash
   ls supabase/functions/
   ```
   Confirm `supabase/functions/<name>/` exists; if not, stop and report.

2. **Pre-flight checks** (report any failure, do not deploy on failure):
   - Type-check if applicable: `npx tsc --noEmit` (or `deno check supabase/functions/<name>/index.ts` if Deno-based).
   - Grep the function for hardcoded secrets (`sk_live`, `sk_test`, `service_role`, raw keys). Secrets must come from `Deno.env.get(...)`, never inline. If found, STOP and surface it.
   - Confirm you are NOT deploying from a dirty unintended state: `git status --short`.

3. **Deploy** using the project-approved command only:
   ```bash
   npx supabase functions deploy <name>
   ```
   Do not improvise alternate deploy paths.

4. **Report** the deploy output. If it failed on missing env/secrets, surface the missing var name — do not hardcode values (CLAUDE.md rule). Remind the user that function secrets are set via `npx supabase secrets set`, not committed.

## Rules
- Edge function changes deploy ONLY via `npx supabase functions deploy <name>`.
- Never inline secrets; read from `Deno.env.get(...)`.
- If a deploy fails on env vars, surface it — never hardcode.
