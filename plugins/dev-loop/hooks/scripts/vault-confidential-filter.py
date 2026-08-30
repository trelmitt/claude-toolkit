#!/usr/bin/env python3
"""vault-confidential-filter — the ONE fail-closed gate that decides whether a vault note may
leave the vault (be indexed, embedded, summarised, or pushed).

Every downstream consumer — the future Smart Connections / RAG index, a weekly-brief composer,
and doctor.sh — must route through `is_confidential(path)` so "confidential" means exactly one
thing in exactly one place. A note is confidential (EXCLUDED) when ANY of:

  1. filename ends in `.local.md`                    (the vault-wide *.local.md convention)
  2. any path component is `_Confidential`           (the dedicated quarantine folder)
  3. frontmatter has `confidential:` truthy          (true/yes/1/on)
  4. it cannot be read, OR it opens a `---` frontmatter block that never closes
                                                     (unverifiable -> FAIL CLOSED -> excluded)

A note that is readable, correctly-formed, and carries none of markers 1-3 is INCLUDED — this
explicitly includes a normal note with no frontmatter at all (absent frontmatter is not an error,
so it is not treated as confidential). Fail-closed applies to the UNVERIFIABLE case (unreadable /
malformed), never to the merely-unmarked case; excluding every unmarked note would empty the index.

Usage:
  vault-confidential-filter.py --check <path>          # prints "confidential" | "included"
  vault-confidential-filter.py --list-included [--root R]
  vault-confidential-filter.py --list-confidential [--root R]   # doctor.sh counts this
  vault-confidential-filter.py --selftest
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

DEFAULT_ROOT = os.path.expanduser("~/obsidian")
_TRUTHY = {"true", "yes", "1", "on"}
_FALSEY = {"false", "no", "0", "off"}  # only these EXPLICIT values mean "not confidential"
# Never walked for listing (app internals / trash / vcs). NOT _Confidential — that IS walked, so it
# shows up in --list-confidential and can be audited.
_SKIP_DIRS = {".git", ".trash", ".obsidian"}


def _frontmatter_confidential(text: str) -> bool:
    """True if a `---` frontmatter block marks the note confidential, or is malformed (fail-closed).

    No frontmatter block at all -> False (absence is not an error). An opened-but-never-closed
    block -> True (cannot verify -> fail closed). A `confidential:` present with a value we cannot
    confidently read as falsey (a truthy value, an inline-commented value like `true # note`, or
    anything unrecognised) -> True: the author reached for the marker, so honour it fail-closed.
    More than one `confidential:` line -> True: a duplicate key is itself ambiguous/malformed, and a
    first-match-wins read would let an early `false` mask a later `true` (a leak). A value opened
    with a quote that never closes on the same line -> True: an unterminated quoted scalar is
    unverifiable (a real YAML parser would reject it), so fail closed rather than read the fragment.

    ponytail: regex line-scan, not a YAML parse — the hook family is deliberately stdlib-only
    (no PyYAML) for pre-commit portability. It fails closed on the realistic vectors; genuinely
    non-mapping frontmatter (a list/scalar body) would need a real parser to reject."""
    if not text.startswith("---"):
        return False
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return False
    close = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if close is None:
        return True  # unterminated frontmatter -> unparseable -> fail closed
    vals = []
    for ln in lines[1:close]:
        m = re.match(r"\s*confidential\s*:\s*(.+?)\s*$", ln, re.IGNORECASE)
        if not m:
            continue
        # Strip an inline YAML comment (whitespace + '#'), then normalise.
        raw = re.sub(r"\s+#.*$", "", m.group(1)).strip()
        q = raw[:1]
        if q in ("'", '"') and (len(raw) < 2 or raw[-1] != q):
            return True  # unterminated quoted value -> unverifiable -> fail closed
        vals.append(raw.strip("\"'").strip().lower())
    if not vals:
        return False            # no marker
    if len(vals) > 1:
        return True             # duplicate confidential: key -> ambiguous -> fail closed
    val = vals[0]
    if val in _TRUTHY:
        return True
    if val in _FALSEY or val == "":
        return False            # explicit non-confidential (or an unset empty value)
    return True                 # present but unverifiable -> fail closed (EXCLUDE)


def is_confidential(path) -> bool:
    """Fail-closed: return True (EXCLUDE) unless the note is verifiably safe to expose."""
    p = Path(path)
    # A symlinked note has a clean lexical path but its CONTENT is read from the target — a
    # `public.md -> _Confidential/board.md` link would otherwise be listed as included and leak
    # the quarantined note. Reject the file-level symlink. (Scoped to the note itself, not every
    # ancestor up to '/': a parent-symlink walk would exclude the whole vault when its root sits
    # under a symlinked path — e.g. macOS /tmp -> /private/tmp — emptying the index.)
    if p.is_symlink():
        return True
    if p.name.lower().endswith(".local.md"):
        return True
    if any(part.lower() == "_confidential" for part in p.parts):
        return True
    try:
        text = p.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return True  # cannot read -> cannot verify -> fail closed
    return _frontmatter_confidential(text)


def _iter_notes(root: Path):
    for p in sorted(root.rglob("*.md")):
        if set(p.relative_to(root).parts) & _SKIP_DIRS:
            continue
        yield p


def _list(root: Path, want_confidential: bool):
    for p in _iter_notes(root):
        if is_confidential(p) == want_confidential:
            print(p.relative_to(root))


def _check(cond, msg) -> None:
    """Explicit assertion that survives `python -O` (which strips bare `assert`), keeping the
    selftest a real verification floor even under optimisation."""
    if not cond:
        raise AssertionError(msg)


def selftest() -> int:
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "_Confidential").mkdir()
        (root / "normal.md").write_text("---\ntype: note\ntags: [x]\n---\nbody\n")
        (root / "no-frontmatter.md").write_text("just a plain note, no frontmatter\n")
        (root / "flagged.md").write_text("---\ntype: note\nconfidential: true\n---\nsecret\n")
        (root / "flagged-yes.md").write_text('---\nconfidential: "yes"\n---\nsecret\n')
        (root / "flagged-comment.md").write_text("---\nconfidential: true # internal\n---\nsecret\n")
        (root / "flagged-unknown.md").write_text("---\nconfidential: maybe\n---\nsecret\n")
        (root / "safe-false.md").write_text("---\nconfidential: false\n---\nok\n")
        (root / "safe-empty.md").write_text("---\nconfidential:\ntype: note\n---\nok\n")
        # Duplicate confidential: key — an early `false` must NOT mask a later `true`.
        (root / "dup-leak.md").write_text("---\nconfidential: false\ntags: [x]\nconfidential: true\n---\nsecret\n")
        (root / "dup-false.md").write_text("---\nconfidential: false\nconfidential: false\n---\nok\n")
        # Unterminated quoted value (CodeRabbit's cited malformed-YAML vector) -> unverifiable.
        (root / "bad-quote.md").write_text('---\nconfidential: "\ntype: note\n---\nx\n')
        (root / "terms.local.md").write_text("no frontmatter but local\n")
        (root / "_Confidential" / "board.md").write_text("no flag but quarantined\n")
        (root / "malformed.md").write_text("---\ntype: note\nnever closes\nbody with no fence\n")
        # A symlink with a clean lexical path pointing into the quarantine folder: its own name has
        # no marker, so only the file-level symlink check keeps its target from leaking.
        os.symlink(root / "_Confidential" / "board.md", root / "public.md")

        _check(is_confidential(root / "flagged.md"), "truthy flag")
        _check(is_confidential(root / "flagged-yes.md"), "quoted yes")
        _check(is_confidential(root / "flagged-comment.md"), "inline-commented truthy value must stay confidential")
        _check(is_confidential(root / "flagged-unknown.md"), "unrecognised confidential value must fail closed")
        _check(is_confidential(root / "terms.local.md"), "by name, no frontmatter needed")
        _check(is_confidential(root / "_Confidential" / "board.md"), "by path")
        _check(is_confidential(root / "public.md"), "symlink into quarantine must not leak")
        _check(is_confidential(root / "malformed.md"), "fail-closed on unterminated frontmatter")
        _check(is_confidential(root / "missing.md"), "unreadable (does not exist) fails closed")
        _check(not is_confidential(root / "normal.md"), "a normal note -> included")
        _check(not is_confidential(root / "no-frontmatter.md"), "absent frontmatter -> included")
        _check(not is_confidential(root / "safe-false.md"), "explicit false -> included")
        _check(not is_confidential(root / "safe-empty.md"), "empty/unset confidential value -> included")
        _check(is_confidential(root / "dup-leak.md"), "duplicate confidential: (false then true) must not leak")
        _check(is_confidential(root / "dup-false.md"), "duplicate confidential: key -> fail closed even when both falsey")
        _check(is_confidential(root / "bad-quote.md"), "unterminated quoted value -> fail closed")

        confidential = {str(p.relative_to(root)) for p in _iter_notes(root) if is_confidential(p)}
        _check("normal.md" not in confidential and "no-frontmatter.md" not in confidential, confidential)
        _check("terms.local.md" in confidential and "_Confidential/board.md" in confidential, confidential)
        _check("public.md" in confidential, "symlink alias must be listed confidential")
    print("selftest: OK")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Fail-closed confidential-note filter for the vault.")
    ap.add_argument("--check", metavar="PATH", help="print 'confidential' or 'included' for one path")
    ap.add_argument("--list-included", action="store_true")
    ap.add_argument("--list-confidential", action="store_true")
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    if args.check:
        print("confidential" if is_confidential(args.check) else "included")
        return 0

    root = Path(os.path.expanduser(args.root)).resolve()
    if not root.is_dir():
        print(f"vault-confidential-filter: root not found: {root}", file=sys.stderr)
        return 2
    if args.list_included:
        _list(root, want_confidential=False)
        return 0
    if args.list_confidential:
        _list(root, want_confidential=True)
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
