# trevor-tools

A shareable [Claude Code](https://docs.claude.com/en/docs/claude-code) marketplace: autonomous
**dev-loop guardrails** plus a **portable skill library** for shipping software. Everything here
works standalone — the few notes-vault features are optional and no-op when no vault is
configured.

## Install

```
/plugin marketplace add trelmitt/claude-toolkit
/plugin install dev-loop@trevor-tools
/plugin install trevor-skills@trevor-tools
```

Then restart Claude Code (or start a new session) so the plugins load.

## What's inside

### `dev-loop` — guardrails + workflow skills
Safety hooks that run automatically, plus a few workflow commands:

- **Protection hooks** (PreToolUse): block pushes/commits to protected branches, block
  force-push, block `.env`/secret writes, and scan for live secrets before a commit.
- **SessionStart**: situational-awareness context + a git-identity guard.
- **Stop**: an end-of-session retro-capture nudge, and an optional deterministic notes-vault
  tidy loop (only runs if you point it at a vault).
- **`supabase-security-reviewer`** subagent: audits pending Supabase + Stripe changes for
  missing RLS, over-permissive policies, leaked service-role keys, and webhook gaps.
- **Skills**: `/ship`, `/babysit-prs`, `/create-migration`, `/deploy-edge-function`, `/retro`,
  `/spike`.

### `trevor-skills` — a skill library
| Skill | What it does |
|---|---|
| `cracked-dev` | Autonomous senior-engineer loop: triage → branch → build → self-audit → PR |
| `skill-forge` | Build, audit, and tune your own Claude skills |
| `sr-security-auditor` | OWASP / HIPAA / SOC2 code audit with a severity-rated report + patches |
| `sr-fullstack-engineer` | Senior full-stack engineering persona and review lens |
| `deep-research` | Structured multi-source research with a report template |
| `build-vs-borrow` | Prior-art gate: DEPEND / FORK / VENDOR / BUILD before you build a commodity capability |
| `oss-scout` | Hunts GitHub / npm / PyPI / Hugging Face for the best existing base, gated by license |
| `migration-safety-reviewer` | Reviews DB migrations for reliability and lock/downtime risk |
| `supabase-rls-test-harness` | Generates pgTAP/Vitest tests for Supabase RLS policies |
| `evaluator` | Quantified scoring rubric for agent/session output |
| `product-idea-generator` | Ranked product/feature ideation |
| `product-strategy-consultant` | One opinionated, decisive strategy recommendation |
| `feature-roadmap-builder` | Turns a feature list into a scored Now/Next/Later roadmap |
| `competitive-analysis` | Structured competitive-set discovery and positioning |
| `time-sense` | Realistic effort/time estimates for agent-built work |

## Notes

- **Standalone by default.** No external services required. A handful of skills can *optionally*
  persist notes to a vault if you set `$CLAUDE_VAULT_DIR`; with it unset, they skip that step.
- **Some hooks are opinionated** (e.g. blocking pushes to `main`/`master`). If a guardrail
  doesn't fit your workflow, disable that plugin or edit its hook.

## License

MIT — see [LICENSE](./LICENSE).
