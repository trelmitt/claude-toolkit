#!/usr/bin/env bun
/**
 * gen-rls-test.ts — deterministic RLS-isolation pgTAP test generator.
 *
 * Renders scripts/templates/{org-isolation,owner-scoped}.sql.tmpl into
 *   supabase/tests/database/NNN-<table>-rls.test.sql
 * with literal synthetic UUIDs, only the requested command blocks, best-effort
 * column introspection for NOT-NULL-without-default columns, and a plan(N) that
 * is COUNTED from the assertions actually emitted (so plan can never drift).
 *
 * Run with bun (the repo's package manager), NOT node/npm:
 *   bun scripts/gen-rls-test.ts --table documents --kind org --tenant-col org_id
 *   bun scripts/gen-rls-test.ts --table profiles --kind owner --owner-col id --owner-is-pk
 *
 * This EMITS a test file; it never edits a policy or runs the DB. After generating,
 * run `supabase test db` (Step 4) and scripts/mutation-check.sh (Step 5).
 */
import { readFileSync, writeFileSync, readdirSync, existsSync, mkdirSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const ALL_CMDS = ["select", "insert", "update", "delete"] as const;
type Cmd = (typeof ALL_CMDS)[number];

function parseArgs(argv: string[]): Record<string, string | boolean> {
  const out: Record<string, string | boolean> = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith("--")) continue;
    const key = a.slice(2);
    const next = argv[i + 1];
    if (next === undefined || next.startsWith("--")) out[key] = true;
    else { out[key] = next; i++; }
  }
  return out;
}

const args = parseArgs(process.argv.slice(2));
const table = String(args.table || "");
if (!table || !/^[a-z_][a-z0-9_]*$/i.test(table)) {
  console.error("ERROR: --table <name> is required and must be a bare identifier (schema is assumed public).");
  process.exit(2);
}
const kind = String(args.kind || "org"); // org | owner
const tenantCol = String(args["tenant-col"] || "org_id");
const ownerCol = String(args["owner-col"] || "id");
const ownerIsPk = Boolean(args["owner-is-pk"]); // owner column IS the PK referencing auth.users (e.g. profiles.id)
const outDir = String(args["out-dir"] || "supabase/tests/database");
const requested = String(args.commands || "select,insert,update,delete")
  .split(",").map((s) => s.trim().toLowerCase()).filter(Boolean) as Cmd[];
const cmds = ALL_CMDS.filter((c) => requested.includes(c));

// ---- literal synthetic ids (committed into the test; deterministic file content) ----
const ALICE = randomUUID();
const BOB = randomUUID();
const ORG_A = randomUUID();
const ORG_B = randomUUID();
// For owner tables where the owner col IS the PK (== auth.users id), the row id IS the user id.
const ROW_A = kind === "owner" && ownerIsPk ? ALICE : randomUUID();
const ROW_B = kind === "owner" && ownerIsPk ? BOB : randomUUID();

// ---- best-effort introspection of extra NOT NULL columns lacking a default ----
// Provided automatically by the template: id, created_by, and the tenant/owner column.
const providedCols = new Set<string>(
  kind === "org" ? ["id", tenantCol, "created_by"] : ["id", ownerCol, "created_by"],
);

function dbUrl(): string | null {
  if (process.env.SUPABASE_DB_URL) return process.env.SUPABASE_DB_URL;
  try {
    const env = execFileSync("supabase", ["status", "-o", "env"], { encoding: "utf8" });
    const m = env.match(/^DB_URL="?([^"\n]+)"?/m);
    return m ? m[1] : null;
  } catch { return null; }
}

function placeholderFor(type: string): string | null {
  const t = type.toLowerCase();
  if (/(char|text|citext|name)/.test(t)) return "''";
  if (/(json)/.test(t)) return "'{}'";
  if (/(bool)/.test(t)) return "false";
  if (/(int|numeric|decimal|real|double|float)/.test(t)) return "0";
  if (/(timestamp|date|time)/.test(t)) return "now()";
  return null; // uuid/enum/array/unknown -> needs a human; emit a loud TODO null
}

interface ExtraCol { name: string; type: string; value: string; todo: boolean; }
function introspectExtra(): ExtraCol[] {
  const url = dbUrl();
  if (!url) {
    console.warn("WARN: no DB url (set SUPABASE_DB_URL or run `supabase start`). Skipping column introspection;\n" +
      "      if the seed INSERT fails on a missing NOT NULL column, add it by hand (Step 4: fix the fixture).");
    return [];
  }
  const q = `select column_name, data_type from information_schema.columns
    where table_schema='public' and table_name='${table}'
      and is_nullable='NO' and column_default is null
    order by ordinal_position;`;
  let rows: string[];
  try {
    rows = execFileSync("psql", [url, "-At", "-F", "\t", "-c", q], { encoding: "utf8" })
      .split("\n").map((l) => l.trim()).filter(Boolean);
  } catch (e) {
    console.warn("WARN: psql introspection failed; skipping. Add extra NOT NULL columns by hand if the seed fails.");
    return [];
  }
  const extra: ExtraCol[] = [];
  for (const line of rows) {
    const [name, type] = line.split("\t");
    if (!name || providedCols.has(name)) continue;
    const ph = placeholderFor(type || "");
    extra.push({ name, type: type || "?", value: ph ?? "null", todo: ph === null });
  }
  return extra;
}

const extra = introspectExtra();
if (extra.some((c) => c.todo)) {
  console.warn("WARN: these NOT NULL columns need a real value — emitted as `null /* TODO */`, the test will fail until you set them:\n" +
    extra.filter((c) => c.todo).map((c) => `        - ${c.name} (${c.type})`).join("\n"));
}
const SEED_EXTRA_COLS = extra.length ? ", " + extra.map((c) => c.name).join(", ") : "";
const seedVals = (which: "A" | "B") =>
  extra.length ? ", " + extra.map((c) => (c.todo ? `null /* TODO ${c.name} (${c.type}) for row ${which} */` : c.value)).join(", ") : "";
// quote-escaped variant for values embedded INSIDE a single-quoted SQL string (the
// throws_ok/lives_ok assertion inserts), where ' must be doubled to ''.
const seedValsQ = (which: "A" | "B") => seedVals(which).replace(/'/g, "''");

// ---- load + render template ----
const tmplName = kind === "owner" ? "owner-scoped.sql.tmpl" : "org-isolation.sql.tmpl";
let sql = readFileSync(join(HERE, "templates", tmplName), "utf8");

// strip unused command blocks
for (const c of ALL_CMDS) {
  if (cmds.includes(c)) continue;
  const re = new RegExp(`-- @cmd:${c}[\\s\\S]*?-- @end:${c}\\n`, "g");
  sql = sql.replace(re, "");
}
// owner: drop the manual seed block when the owner row is auto-created (owner col is the PK)
if (kind === "owner" && ownerIsPk) {
  sql = sql.replace(/-- @seed:owner[\s\S]*?-- @end:seed\n/g, "");
}
// drop any remaining block-marker comment lines (openers carry trailing dashes)
sql = sql.replace(/^[ \t]*-- @(cmd|end|seed):[a-z]+.*\r?\n/gim, "");

// owner INSERT forge: an owner-is-pk table can't be "owned by bob" without a PK collision,
// so forge a row alice simply doesn't own (random id); a separate owner column forges bob's.
const FORGE_COLS = kind === "owner" ? (ownerIsPk ? "id" : `id, ${ownerCol}`) : "";
const FORGE_VALS = kind === "owner" ? (ownerIsPk ? "gen_random_uuid()" : `gen_random_uuid(), ''${BOB}''`) : "";

// token substitution (do PLAN last, after counting)
const subs: Record<string, string> = {
  TABLE: table, TENANT_COL: tenantCol, OWNER_COL: ownerCol,
  ALICE, BOB, ORG_A, ORG_B, ROW_A, ROW_B, FORGE_COLS, FORGE_VALS,
  SEED_EXTRA_COLS, SEED_EXTRA_VALS_A: seedVals("A"), SEED_EXTRA_VALS_B: seedVals("B"),
  SEED_EXTRA_VALS_A_Q: seedValsQ("A"), SEED_EXTRA_VALS_B_Q: seedValsQ("B"),
};
for (const [k, v] of Object.entries(subs)) sql = sql.replaceAll(`{{${k}}}`, v);

// count assertions -> plan(N). Counts lines that START a pgTAP assertion call.
const ASSERT = /^\s*select\s+(results_eq|results_ne|set_eq|set_ne|isnt_empty|is_empty|throws_ok|throws_like|lives_ok|isnt|is|ok|matches)\s*\(/gim;
const planN = (sql.match(ASSERT) || []).length;
if (planN === 0) { console.error("ERROR: rendered 0 assertions — check --commands."); process.exit(1); }
sql = sql.replace("{{PLAN}}", String(planN));

// ---- write to supabase/tests/database/NNN-<table>-rls.test.sql ----
if (!existsSync(outDir)) mkdirSync(outDir, { recursive: true });
const nums = readdirSync(outDir)
  .map((f) => (f.match(/^(\d+)/) ? parseInt(f.match(/^(\d+)/)![1], 10) : -1))
  .filter((n) => n >= 0);
const next = String((nums.length ? Math.max(...nums) : 0) + 1).padStart(3, "0");
const outPath = join(outDir, `${next}-${table}-rls.test.sql`);
writeFileSync(outPath, sql);

console.log(`Wrote ${outPath}`);
console.log(`  kind=${kind}  commands=${cmds.join(",")}  plan(${planN})  extra-cols=${extra.length}`);
console.log(`Next: run \`supabase test db\` (Step 4), then scripts/mutation-check.sh ${table} (Step 5 — prove it has teeth).`);
