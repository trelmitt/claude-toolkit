#!/usr/bin/env python3
"""vault-tidy — deterministic Obsidian-vault hygiene for the approved Stop-hook loop.

Scope is fixed by [[Decision - Human-in-the-loop autonomy]] (2026-07-16 amendment):
MECHANICAL, verifiable-in-a-diff work ONLY. This engine never invents content and never
writes a file it did not compute a concrete fix for.

  (a) wikilinks + orphans
      - auto-fix a broken [[link]] ONLY when it resolves to exactly ONE existing note under
        case-/whitespace-insensitive matching (e.g. [[oss radar]] -> [[OSS Radar]]). Obvious
        in a diff, near-zero false-positive rate.
      - ambiguous (>1 candidate) or unresolvable (0 candidate) links: REPORT only.
      - orphans (no inlinks AND no outlinks): REPORT only. Linking one is judgment -> out of scope.
  (b) frontmatter normalisation (type, tags, surface)
      - tags written as a bare scalar / comma string -> a YAML flow list; dedup; drop empties.
      - a MISSING type/surface value is REPORTED, never invented.

Deterministic by design: the local 35B is not needed for a+b, and an LLM guess is exactly what
would fail the "merged unedited 3x" stopping condition. The model earns its place only when the
judgment scope (contradiction-flagging / lesson-capture) is added later.

Modes:
  --report   (default) read-only; prints a human summary + a machine JSON line. exit 0.
  --apply    write auto-fixes to the tree; print one changed path (relative to root) per line
             to stdout so a caller can `git add` exactly those files. exit 0.
  --selftest run assertions on temp fixtures. exit 0 on pass, 1 on fail.

Usage:
  vault-tidy.py [--report|--apply] [--root /path/to/vault] [--json]
  vault-tidy.py --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

DEFAULT_ROOT = os.path.expanduser("~/obsidian")

# Wikilink: matches [[target]] and ![[embed]], capturing everything up to the closing ]].
# We then split off a |alias and a #heading to get the bare note target.
_WIKILINK_RE = re.compile(r"!?\[\[([^\[\]\n]+?)\]\]")
_FENCE_RE = re.compile(r"```.*?```|~~~.*?~~~", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")


def _strip_code(text: str) -> str:
    """Blank out fenced + inline code so links inside code are not treated as real links.

    Replaces code spans with same-length blanks (keeps offsets stable, though we don't rely on
    offsets after this — we re-scan the stripped text)."""
    text = _FENCE_RE.sub(lambda m: " " * len(m.group(0)), text)
    text = _INLINE_CODE_RE.sub(lambda m: " " * len(m.group(0)), text)
    return text


def _norm(name: str) -> str:
    """Normalisation key for case-/whitespace-insensitive note matching."""
    return re.sub(r"\s+", " ", name.strip()).lower()


def link_target(raw: str) -> str:
    """From the inside of [[...]], return the bare note name (strip |alias and #heading).

    A same-file heading link like [[#Section]] yields '' and is ignored by callers."""
    target = raw.split("|", 1)[0]
    target = target.split("#", 1)[0]
    # Path-qualified links ([[folder/Note]]) resolve by basename in Obsidian.
    target = target.split("/")[-1]
    return target.strip()


# Asset extensions: an embed ![[x.ext]] pointing at one of these is a file embed, NOT a broken
# note link, so it must not flood the unresolvable report. An allowlist (not a bare "has a dot")
# so a dotted NOTE name like [[v1.2 plan]] is still treated as a note.
_ASSET_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".avif", ".bmp", ".ico",
    ".pdf", ".mp4", ".mov", ".m4v", ".webm", ".mp3", ".wav", ".m4a", ".ogg", ".flac",
    ".xlsx", ".xls", ".csv", ".zip", ".heic", ".excalidraw", ".canvas",
}


def _is_asset(target: str) -> bool:
    return os.path.splitext(target)[1].lower() in _ASSET_EXTS


# Dirs excluded from BOTH the note index and the source scan. Templates/ is excluded because
# template files intentionally hold placeholder links ([[Project: ...]]), placeholder
# frontmatter, and are structurally orphans — scanning them is pure noise.
EXCLUDED_DIRS = {".git", ".trash", ".obsidian", "Templates"}


def iter_md_files(root: Path):
    for p in sorted(root.rglob("*.md")):
        parts = set(p.relative_to(root).parts)
        if parts & EXCLUDED_DIRS:
            continue
        yield p


def build_index(root: Path):
    """Return (name_index, dup_norms).

    name_index maps _norm(basename) -> {"names": {actual basenames}, "files": {rel paths}}.
    Keyed by FILE identity (rel path), so two notes with the same stem in different folders
    (Areas/Roadmap.md + Projects/Roadmap.md — normal in PARA) register as >1 file and land in
    `dup`; any link to a dup norm-key is ambiguous and must never be auto-fixed."""
    index = {}
    for p in iter_md_files(root):
        entry = index.setdefault(_norm(p.stem), {"names": set(), "files": set()})
        entry["names"].add(p.stem)
        entry["files"].add(str(p.relative_to(root)))
    dup = {k for k, v in index.items() if len(v["files"]) > 1}
    return index, dup


def analyse(root: Path):
    """Scan the vault. Return a findings dict; does not write anything."""
    root = Path(root)
    index, dup = build_index(root)

    outlinks = {}   # rel -> set(resolved norm-keys); non-empty => links to a real note
    inlinks = {}    # rel -> set(source rel); who links TO this file
    autofix = []      # {file, raw, from, to}
    ambiguous = []    # {file, target, candidates}
    unresolvable = []  # {file, target}
    fm_missing = []   # {file, field}
    fm_absent = set()  # rel of notes with NO frontmatter block at all (the worst hygiene miss)
    fm_tags_scalar = []  # {file, current, fixed}

    files = list(iter_md_files(root))

    for p in files:
        rel = str(p.relative_to(root))
        try:
            text = p.read_text(encoding="utf-8-sig")  # utf-8-sig transparently strips a BOM
        except (UnicodeDecodeError, OSError):
            continue

        # --- frontmatter ---
        fm = _extract_frontmatter(text)
        if fm is None:
            fm_absent.add(rel)  # no frontmatter block -> counts as missing frontmatter
        else:
            for field in ("type", "surface"):
                if _fm_missing(fm, field):
                    fm_missing.append({"file": rel, "field": field})
            scalar = _tags_scalar_fix(fm)
            if scalar is not None:
                fm_tags_scalar.append({"file": rel, "current": scalar[0], "fixed": scalar[1]})

        # --- wikilinks (scan code-stripped text so code examples are never treated as links) ---
        scan = _strip_code(text)
        outs = set()
        for m in _WIKILINK_RE.finditer(scan):
            raw = m.group(1)
            is_embed = m.group(0).startswith("!")
            target = link_target(raw)
            if not target:
                continue  # same-file heading link ([[#Section]])
            if is_embed and _is_asset(target):
                continue  # ![[image.png]] / ![[report.pdf]] — a file embed, not a note link
            nkey = _norm(target)
            if nkey in index:
                entry = index[nkey]
                outs.add(nkey)
                if target not in entry["names"]:
                    # normalised match but not exact spelling
                    if nkey in dup:
                        ambiguous.append({"file": rel, "target": target, "candidates": sorted(entry["names"])})
                    else:
                        autofix.append({"file": rel, "raw": raw, "from": target, "to": next(iter(entry["names"]))})
                # else exact spelling — fine
            else:
                unresolvable.append({"file": rel, "target": target})
        outlinks[rel] = outs
        for nkey in outs:
            for tgt_rel in index[nkey]["files"]:
                if tgt_rel != rel:
                    inlinks.setdefault(tgt_rel, set()).add(rel)

    orphans = []
    for p in files:
        rel = str(p.relative_to(root))
        if not outlinks.get(rel) and not inlinks.get(rel):
            orphans.append(rel)

    # --- health metrics: cheap ratios over the same scan, for the .vault-health trend log.
    # Derived only from data this pass already built (no second walk). Top-level-folder match on
    # "inbox"/"archive" so it survives PARA renames (00-Inbox, 1-Projects/Archive, …).
    rels = [str(p.relative_to(root)) for p in files]
    n = len(rels) or 1
    # a note is "missing frontmatter" if it has NONE at all, or has a block but lacks type/surface
    fm_missing_files = fm_absent | {m["file"] for m in fm_missing}
    total_backlinks = sum(len(inlinks.get(r, ())) for r in rels)
    def _top(r): return r.split("/", 1)[0].lower()
    metrics = {
        "file_count": len(files),
        "orphan_pct": round(100 * len(orphans) / n, 1),
        "frontmatter_missing_pct": round(100 * len(fm_missing_files) / n, 1),
        "avg_backlinks": round(total_backlinks / n, 2),
        "inbox_count": sum(1 for r in rels if "inbox" in _top(r)),
        "archive_ratio": round(100 * sum(1 for r in rels if "archive" in _top(r)) / n, 1),
    }

    return {
        "root": str(root),
        "file_count": len(files),
        "autofix_links": autofix,
        "ambiguous_links": ambiguous,
        "unresolvable_links": unresolvable,
        "orphans": sorted(orphans),
        "frontmatter_missing": fm_missing,
        "frontmatter_tags_scalar": fm_tags_scalar,
        "dup_basenames": sorted(dup),
        "metrics": metrics,
    }


# --------------------------- frontmatter helpers ---------------------------

def _extract_frontmatter(text: str):
    """Return the raw frontmatter block (list of lines, without the --- fences) or None."""
    if not text.startswith("---"):
        return None
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines[1:i]
    return None


def _fm_missing(fm_lines, field: str) -> bool:
    """True if `field:` is absent or present with an empty value."""
    for ln in fm_lines:
        m = re.match(rf"\s*{re.escape(field)}\s*:\s*(.*)$", ln)
        if m:
            return m.group(1).strip() in ("", "~", "null")
    return True


def _tags_scalar_fix(fm_lines):
    """If tags is a bare scalar / comma string that should be a flow list, return (current, fixed).

    Returns None if tags is absent, already a flow list ([..]), or a block list (- item)."""
    for idx, ln in enumerate(fm_lines):
        m = re.match(r"(\s*)tags\s*:\s*(.*)$", ln)
        if not m:
            continue
        indent, val = m.group(1), m.group(2).strip()
        if val == "":
            # could be a block list on following lines -> leave alone
            return None
        if val.startswith("["):
            # already a flow list; optionally dedup
            items = _parse_flow_list(val)
            deduped = _dedup(items)
            if deduped != items:
                return (val, "[" + ", ".join(deduped) + "]")
            return None
        # bare scalar or comma-separated -> flow list
        items = _dedup([t.strip() for t in val.split(",") if t.strip()])
        if not items:
            return None
        return (val, "[" + ", ".join(items) + "]")
    return None


def _parse_flow_list(val: str):
    inner = val.strip()
    if inner.startswith("["):
        inner = inner[1:]
    if inner.endswith("]"):
        inner = inner[:-1]
    return [t.strip() for t in inner.split(",") if t.strip()]


def _dedup(items):
    seen = set()
    out = []
    for it in items:
        if it.lower() not in seen:
            seen.add(it.lower())
            out.append(it)
    return out


# --------------------------- apply ---------------------------

def apply_fixes(root: Path, findings: dict) -> list[str]:
    """Apply auto-fixable changes to the tree. Return sorted list of changed rel paths."""
    root = Path(root)
    changed: set[str] = set()

    # Group link fixes by file so each file is rewritten once.
    by_file: dict[str, list[dict]] = {}
    for fx in findings["autofix_links"]:
        by_file.setdefault(fx["file"], []).append(fx)
    for fx in findings["frontmatter_tags_scalar"]:
        by_file.setdefault(fx["file"], [])  # ensure key

    for rel in by_file:
        p = root / rel
        try:
            text = p.read_text(encoding="utf-8-sig")  # utf-8-sig strips a BOM if present
        except (UnicodeDecodeError, OSError):
            continue
        new = text

        # link fixes: replace [[from...]] -> [[to...]] preserving alias/heading.
        for fx in by_file[rel]:
            if "from" not in fx:
                continue
            new = _rewrite_link(new, fx["from"], fx["to"])

        # tags scalar fix for this file
        for tf in findings["frontmatter_tags_scalar"]:
            if tf["file"] == rel:
                new = _rewrite_tags(new, tf["current"], tf["fixed"])

        if new != text:
            # Preserve the file's original line ending so a one-line fix is a one-line diff
            # (read_text universal-newlines has already normalised CRLF->LF in `new`).
            nl = "\r\n" if b"\r\n" in p.read_bytes() else "\n"
            with p.open("w", encoding="utf-8", newline=nl) as fh:
                fh.write(new)
            changed.add(rel)

    return sorted(changed)


def _rewrite_link(text: str, frm: str, to: str) -> str:
    """Rewrite the target part of [[frm]] / [[frm|alias]] / [[frm#h]] (and ![[...]]) to `to`.

    Only rewrites links whose bare target normalises to frm; leaves alias/heading intact.
    Code-aware: a match falling inside a fenced or inline code span is left verbatim — analyse()
    ignores code, so apply() must too, or it would silently mutate documentation examples."""
    masked = _strip_code(text)  # equal-length; code spans blanked to spaces (offset-stable)

    def repl(m):
        # a match inside code has its slice blanked in `masked` -> leave it untouched
        if masked[m.start():m.end()] != m.group(0):
            return m.group(0)
        raw = m.group(1)
        if _norm(link_target(raw)) != _norm(frm):
            return m.group(0)
        prefix = m.group(0)[: m.start(1) - m.start(0)]  # '[[' or '![['
        suffix = "]]"
        # rebuild: replace the target segment, keep |alias and #heading
        rest = raw
        alias = heading = ""
        if "|" in rest:
            rest, alias = rest.split("|", 1)
            alias = "|" + alias
        if "#" in rest:
            _, heading = rest.split("#", 1)
            heading = "#" + heading
        return f"{prefix}{to}{heading}{alias}{suffix}"

    return _WIKILINK_RE.sub(repl, text)


def _rewrite_tags(text: str, current: str, fixed: str) -> str:
    fm = _extract_frontmatter(text)
    if fm is None:
        return text
    lines = text.splitlines(keepends=True)
    out = []
    in_fm = False
    seen_close = False
    fence = 0
    for ln in lines:
        stripped = ln.rstrip("\n")
        if stripped.strip() == "---" and fence < 2:
            fence += 1
            out.append(ln)
            continue
        if fence == 1 and not seen_close:
            m = re.match(r"(\s*tags\s*:\s*)(.*)$", stripped)
            if m and m.group(2).strip() == current.strip():
                nl = "\n" if ln.endswith("\n") else ""
                out.append(f"{m.group(1)}{fixed}{nl}")
                continue
        out.append(ln)
    return "".join(out)


# --------------------------- reporting ---------------------------

def summarise(f: dict) -> str:
    lines = []
    lines.append(f"vault-tidy report — {f['file_count']} notes under {f['root']}")
    lines.append(f"  auto-fixable broken links : {len(f['autofix_links'])}")
    lines.append(f"  ambiguous links (report)  : {len(f['ambiguous_links'])}")
    lines.append(f"  unresolvable links (report): {len(f['unresolvable_links'])}")
    lines.append(f"  tags scalar->list (fix)   : {len(f['frontmatter_tags_scalar'])}")
    lines.append(f"  orphans (report)          : {len(f['orphans'])}")
    lines.append(f"  frontmatter missing type/surface (report): {len(f['frontmatter_missing'])}")
    if f["dup_basenames"]:
        lines.append(f"  duplicate basenames       : {len(f['dup_basenames'])}")
    for fx in f["autofix_links"][:20]:
        lines.append(f"    fix  {fx['file']}: [[{fx['from']}]] -> [[{fx['to']}]]")
    return "\n".join(lines)


def has_fixes(f: dict) -> bool:
    return bool(f["autofix_links"] or f["frontmatter_tags_scalar"])


def write_metrics(findings: dict, root) -> Path:
    """Append one JSON line of health metrics to <root>/.vault-health/metrics.jsonl.

    Per-device local trend data: .vault-health/ is git-ignored and, as a dotfolder, not carried by
    Obsidian Sync — so appends never collide across devices. The weekly audit renders the trend."""
    from datetime import datetime, timezone
    out_dir = Path(root) / ".vault-health"
    out_dir.mkdir(exist_ok=True)
    line = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **findings["metrics"]}
    path = out_dir / "metrics.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")
    return path


# --------------------------- selftest ---------------------------

def selftest() -> int:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "OSS Radar.md").write_text("---\ntype: reference\ntags: [strategy]\nsurface: code\n---\n# OSS Radar\nLinks to [[mlx-lm]].\n")
        (root / "mlx-lm.md").write_text("---\ntype: reference\ntags: claude-code\n---\nSee [[oss radar]] and [[Nonexistent Note]].\n")
        (root / "Lonely.md").write_text("---\ntype: note\n---\nNo links here. `[[not a link]]` in code.\n")
        f = analyse(root)

        # [[oss radar]] normalises to OSS Radar -> auto-fixable
        assert any(x["from"] == "oss radar" and x["to"] == "OSS Radar" for x in f["autofix_links"]), f["autofix_links"]
        # [[Nonexistent Note]] -> unresolvable
        assert any(x["target"] == "Nonexistent Note" for x in f["unresolvable_links"]), f["unresolvable_links"]
        # mlx-lm has `tags: claude-code` scalar -> fix to [claude-code]
        assert any(x["file"] == "mlx-lm.md" and x["fixed"] == "[claude-code]" for x in f["frontmatter_tags_scalar"]), f["frontmatter_tags_scalar"]
        # mlx-lm missing surface -> reported
        assert any(x["file"] == "mlx-lm.md" and x["field"] == "surface" for x in f["frontmatter_missing"]), f["frontmatter_missing"]
        # Lonely.md: the [[not a link]] is inside inline code -> NOT a link -> orphan
        assert "Lonely.md" in f["orphans"], f["orphans"]
        # inline-code link must not create an outlink/resolve
        assert not any(x["target"] == "not a link" for x in f["unresolvable_links"]), "code link leaked"

        # apply is idempotent and fixes the two files
        changed = apply_fixes(root, f)
        assert "mlx-lm.md" in changed, changed
        after = (root / "mlx-lm.md").read_text()
        assert "[[OSS Radar]]" in after, after
        assert "tags: [claude-code]" in after, after
        f2 = analyse(root)
        assert not has_fixes(f2), "not idempotent: " + json.dumps(f2)
        changed2 = apply_fixes(root, f2)
        assert changed2 == [], changed2

        # alias + heading preservation
        (root / "A.md").write_text("x [[oss radar#Runs|the radar]] y\n")
        f3 = analyse(root)
        apply_fixes(root, f3)
        assert "[[OSS Radar#Runs|the radar]]" in (root / "A.md").read_text(), (root / "A.md").read_text()

        # code-aware APPLY: a fixable prose link is fixed; fenced + inline copies are untouched
        (root / "Doc.md").write_text(
            "---\ntype: note\ntags: [x]\nsurface: code\n---\n"
            "Prose [[oss radar]] link.\n"
            "Inline `[[oss radar]]` copy.\n"
            "```\n[[oss radar]]\n```\n"
        )
        apply_fixes(root, analyse(root))
        doc = (root / "Doc.md").read_text()
        assert doc.count("[[OSS Radar]]") == 1, doc   # only the prose link
        assert doc.count("[[oss radar]]") == 2, doc   # inline + fenced left verbatim

        # fenced-only link is not a real link -> file stays an orphan, link never resolves
        (root / "Fenced.md").write_text("---\ntype: note\n---\ntext\n```\n[[OSS Radar]]\n```\n")
        ff = analyse(root)
        assert "Fenced.md" in ff["orphans"], ff["orphans"]
        assert not any(x["file"] == "Fenced.md" for x in ff["autofix_links"]), "fenced link leaked"

        # duplicate basenames across folders: ambiguous (never auto-fixed) + no false orphan
        (root / "sub1").mkdir()
        (root / "sub2").mkdir()
        (root / "sub1" / "Note.md").write_text("body linking [[OSS Radar]]\n")
        (root / "sub2" / "note.md").write_text("body\n")
        (root / "Ref.md").write_text("See [[NOTE]] here.\n")
        fdup = analyse(root)
        assert "note" in fdup["dup_basenames"], fdup["dup_basenames"]
        assert any(x["target"] == "NOTE" for x in fdup["ambiguous_links"]), fdup["ambiguous_links"]
        assert not any(x.get("from") == "NOTE" for x in fdup["autofix_links"]), "dup link auto-fixed"
        assert "sub1/Note.md" not in fdup["orphans"], fdup["orphans"]  # it has a real outlink

        # tags flow-list dedup (case-insensitive)
        (root / "Tagged.md").write_text("---\ntype: note\ntags: [alpha, Alpha, beta, beta]\nsurface: code\n---\nbody\n")
        ft = analyse(root)
        assert any(x["file"] == "Tagged.md" and x["fixed"] == "[alpha, beta]" for x in ft["frontmatter_tags_scalar"]), ft["frontmatter_tags_scalar"]

        # present-but-empty type/surface counts as missing
        (root / "Blank.md").write_text("---\ntype: ~\nsurface:\n---\nbody\n")
        fb = analyse(root)
        assert any(x["file"] == "Blank.md" and x["field"] == "type" for x in fb["frontmatter_missing"]), fb["frontmatter_missing"]
        assert any(x["file"] == "Blank.md" and x["field"] == "surface" for x in fb["frontmatter_missing"]), fb["frontmatter_missing"]

        # CRLF preserved on apply: a one-line fix is a one-line diff, not whole-file churn
        (root / "Crlf.md").write_bytes(
            b"---\r\ntype: note\r\ntags: [x]\r\nsurface: code\r\n---\r\nlink [[oss radar]] x\r\nsecond\r\n")
        apply_fixes(root, analyse(root))
        raw = (root / "Crlf.md").read_bytes()
        assert b"[[OSS Radar]]" in raw, raw
        assert b"\r\n" in raw and raw.count(b"\n") == raw.count(b"\r\n"), "CRLF not preserved"

        # non-markdown embeds are not flagged as unresolvable
        (root / "Embed.md").write_text("![[diagram.png]] and ![[sheet.xlsx]]\n")
        fe = analyse(root)
        assert not any(x["target"] in ("diagram.png", "sheet.xlsx") for x in fe["unresolvable_links"]), fe["unresolvable_links"]

        # health metrics: present, fully keyed, and append-able as one JSON line
        m = analyse(root)["metrics"]
        for k in ("file_count", "orphan_pct", "frontmatter_missing_pct", "avg_backlinks", "inbox_count", "archive_ratio"):
            assert k in m, m
        mpath = write_metrics(analyse(root), root)
        assert mpath.exists(), mpath
        last = json.loads(mpath.read_text().splitlines()[-1])
        assert last["file_count"] == m["file_count"] and "ts" in last, last
        # inbox/archive folder detection is top-level and rename-robust
        (root / "00-Inbox").mkdir()
        (root / "00-Inbox" / "Scratch.md").write_text("---\ntype: note\n---\nx\n")
        assert analyse(root)["metrics"]["inbox_count"] == 1, analyse(root)["metrics"]
        # frontmatter_missing_pct must count a note with NO frontmatter, not only one missing a field
        with tempfile.TemporaryDirectory() as d2:
            r2 = Path(d2)
            (r2 / "bare.md").write_text("no frontmatter at all\n")
            (r2 / "full.md").write_text("---\ntype: note\nsurface: code\n---\nok\n")
            pct = analyse(r2)["metrics"]["frontmatter_missing_pct"]
            if pct != 50.0:  # 1 of 2 notes has no frontmatter; explicit raise survives `python -O`
                raise AssertionError(f"no-frontmatter note uncounted in frontmatter_missing_pct: {pct}")

    print("selftest: OK")
    return 0


# --------------------------- main ---------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Deterministic Obsidian-vault hygiene.")
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--apply", action="store_true", help="write auto-fixes (default: report only)")
    ap.add_argument("--report", action="store_true", help="report only (default)")
    ap.add_argument("--json", action="store_true", help="emit machine JSON to stdout")
    ap.add_argument("--metrics", action="store_true",
                    help="append one health-metrics line to <root>/.vault-health/metrics.jsonl")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    root = Path(os.path.expanduser(args.root)).resolve()
    if not root.is_dir():
        print(f"vault-tidy: root not found: {root}", file=sys.stderr)
        return 2

    findings = analyse(root)

    if args.metrics:
        print(write_metrics(findings, root))
        return 0

    if args.apply:
        changed = apply_fixes(root, findings)
        for c in changed:
            print(c)  # one changed rel path per line, for the caller to `git add`
        return 0

    # report mode
    if args.json:
        print(json.dumps(findings))
    else:
        print(summarise(findings))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
