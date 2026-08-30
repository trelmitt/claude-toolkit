# CI wiring — making the suite a *real* regression floor

The whole point of this skill is a test that **goes red in CI when a policy regresses**. But
a red check only blocks a merge if it is **required**. This repo's dev-loop merges with
`gh pr merge --auto --squash`, and GitHub auto-merge waits **only on required status checks**.
So:

> A `db-tests` job that exists but is not a required check is **advisory** — a PR that
> regresses RLS will auto-merge with the suite failing. The floor does not exist until the
> required-status-check context is applied to the default branch.

Be honest about this in every hand-off. Do not claim "wired into CI as a regression floor"
until the protection below is in place and verified.

## 1. Add the job (autonomous, feature branch)
Copy `scripts/db-tests.workflow.yml` to `.github/workflows/db-tests.yml`. It is **additive**
— it does not modify the existing `ci.yml`. It uses `supabase/setup-cli@v1` + `supabase db
start` (boots local Postgres, applies `supabase/migrations`) + `supabase test db`. Detect
the repo's default branch — don't assume `main` (some repos use another).

The job `name:` is **load-bearing** — it becomes the required-check `context`. Keep it
stable (`db-tests`).

## 2. Make it required (HUMAN, on the shared branch)
Applying branch protection to the shared default branch is **outside the autonomous write
floor** (the dev-loop allows feature-branch commits + auto-merge, not shared-branch
protection changes). The skill **prints** this for the user to run:

```bash
gh api -X PUT repos/<owner>/<repo>/branches/<default-branch>/protection --input - <<'JSON'
{ "required_status_checks": { "strict": true, "checks": [ { "context": "db-tests" } ] },
  "enforce_admins": false, "required_pull_request_reviews": null, "restrictions": null }
JSON
```

(If protection already exists, merge `db-tests` into the existing `checks[]` rather than
overwriting — fetch the current protection first.)

## 3. Verify enforcement (don't trust, check)
After the first PR runs the job:
- `gh pr checks <pr>` must list a check named **exactly** `db-tests`. A name mismatch means
  the required context is "expected" but never reported, and **PRs hang forever**.
- `gh api repos/<owner>/<repo>/branches/<default-branch>/protection` must show `db-tests` under
  `required_status_checks.checks[].context`.

Only once both are true is the regression floor real. State that explicitly in the PR.

## Cost note
`supabase db start` + `supabase test db` adds a Docker dependency and minutes to each PR.
Keep it a **separate job** from the bun-only `ci.yml` so it can be tuned/parallelized
independently without slowing typecheck/lint.
