# trevor-tools

A shareable [Claude Code](https://docs.claude.com/en/docs/claude-code) marketplace: autonomous
**dev-loop guardrails** plus a **portable skill library** for shipping software. Everything here
works standalone — the few notes-vault features are optional and no-op when no vault is configured.

## Install

```
/plugin marketplace add trelmitt/claude-toolkit
/plugin install dev-loop@trevor-tools
/plugin install trevor-skills@trevor-tools
```

Then restart Claude Code (or start a new session) so the plugins load.

## How your Claude uses these

You don't have to memorize anything. Once installed, **Claude reads every skill's description and
invokes the right one on its own** when your request matches — ask "is there a library that already
does this?" and `oss-scout` fires; ask "audit this for security" and `sr-security-auditor` fires.

Three ways to drive them, from least to most explicit:

1. **Just describe the task.** Claude auto-triggers the matching skill. This is the default and
   usually all you need.
2. **Name it:** "use the `build-vs-borrow` skill on this" — forces that skill even if Claude
   wouldn't have reached for it.
3. **Slash command:** `/trevor-skills:<name>` (e.g. `/trevor-skills:cracked-dev`) — invoke directly.

Skills also **chain**: `cracked-dev` calls `build-vs-borrow` before writing a commodity feature,
runs `sr-security-auditor` before every commit, and hands lessons to `skill-forge`. You can compose
them the same way in plain language ("research the options, then estimate the effort").

**Portable & standalone.** No accounts, no external services. A few skills can *optionally* save
notes to a vault if you set `$CLAUDE_VAULT_DIR`; unset, they simply skip that step. Nothing here
depends on the author's private setup.

## `trevor-skills` — the skill library

Grouped by what you'd reach for. All 15 are independent — install once, use any.

### Ship & engineer
| Skill | Reach for it when… |
|---|---|
| `cracked-dev` | You want a hands-off senior-engineer loop: triage the repo → rank the highest-leverage work → branch, build, self-audit, open a PR — repeat. Has hard safety fences and a security gate before every commit. |
| `sr-fullstack-engineer` | You want reviews and implementation held to a senior full-stack bar (architecture, edge cases, maintainability), not just "make it work." |

### Decide before building
| Skill | Reach for it when… |
|---|---|
| `build-vs-borrow` | You're about to build a commodity capability (auth, rate limiter, parser, job queue, CSV/PDF, search). Gives a decisive DEPEND / FORK / VENDOR / BUILD verdict so you don't reinvent a hardened library. |
| `oss-scout` | You want the best *existing* base first — hunts GitHub / npm / PyPI / Hugging Face, ranked and license-gated, before you write a line. |

### Ship safely (security + database)
| Skill | Reach for it when… |
|---|---|
| `sr-security-auditor` | You want a severity-rated security pass (OWASP / HIPAA / SOC2 lens) on a diff or file, with concrete patches — not vibes. |
| `migration-safety-reviewer` | You're changing a DB schema and need to catch lock/downtime/destructive risk *before* it hits prod. |
| `supabase-rls-test-harness` | You changed a Supabase RLS policy and want committed pgTAP/Vitest tests that **prove** tenant A can't read tenant B's rows — and go red if isolation breaks. |

### Product & strategy
| Skill | Reach for it when… |
|---|---|
| `product-idea-generator` | You want ranked product/feature ideas instead of a brainstorm dump. |
| `product-strategy-consultant` | You want one opinionated, decisive strategy recommendation — not five hedged options. |
| `feature-roadmap-builder` | You have a pile of features and need a scored Now / Next / Later roadmap. |
| `competitive-analysis` | You need to map the competitive set and find your positioning, structured. |

### Research, estimation & meta
| Skill | Reach for it when… |
|---|---|
| `deep-research` | You want structured, multi-source research with a real report — not a single web search. |
| `time-sense` | You want a realistic effort/time estimate for agent-built work, in concrete units. |
| `evaluator` | You want a quantified scoring rubric applied to agent or session output. |
| `skill-forge` | You want to build, audit, or tune your *own* Claude skills (this is how the ones here were made). |

## `dev-loop` — guardrails + workflow

Safety hooks that run automatically, plus a few workflow commands. Opinionated on purpose — if a
guardrail doesn't fit, disable the plugin or edit the hook.

- **Protection hooks** (run before tool calls): block pushes/commits to protected branches, block
  force-push, block `.env`/secret writes, and scan for live secrets before a commit.
- **Session hooks**: situational-awareness context at start; a git-identity guard; an
  end-of-session retro nudge; an optional notes-vault tidy (only if you point it at a vault).
- **`supabase-security-reviewer`** subagent: audits pending Supabase + Stripe changes for missing
  RLS, over-permissive policies, leaked service-role keys, and webhook gaps.
- **Commands**: `/ship`, `/babysit-prs`, `/create-migration`, `/deploy-edge-function`, `/retro`,
  `/spike`.

## License

MIT — see [LICENSE](./LICENSE). Use, copy, modify, and share freely; no warranty. The single
repo-level license covers every plugin and skill inside it.
