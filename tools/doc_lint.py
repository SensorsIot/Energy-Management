#!/usr/bin/env python3
"""Portable documentation linter for the three-plane doc model.

The `documentation` skill states the present-state / one-canonical-home rules; this linter is what
makes them stick once nobody is watching. It is deliberately config-light: point it at one or more
doc roots and it checks the few things a doc reliably drifts on:

  present-state   — history narrative ("previously", "as of 2025-…", "v1 vs v2") and rationale
                    narrative ("we chose X because", "trade-off") that the model is tempted to add
                    on an UPDATE. Present-tense rules only; git log holds the history.
  broken-link     — relative Markdown links whose target file does not exist (cross-references are
                    the connective tissue of the model; a dead link silently breaks routing).
  duplication     — the same substantial sentence living in two docs, i.e. a canonical fact
                    restated instead of linked (advisory — the one-canonical-home rule).

It is intentionally forgiving: fenced code blocks are skipped, and content under an explicit
`## Changelog`, `## Cleanup-notes`, `## Target`, or anything beneath an `Archive/` path is exempt
(those are the sanctioned homes for dated / historical / not-yet-built material).

Exit status is 0 when no errors are found, 1 otherwise — so it can gate a pre-commit hook.
Warnings (duplication is a warning by default) do not fail the run unless --strict is given.

Usage:
    doc_lint.py [ROOT ...] [--files f1.md f2.md] [--strict] [--quiet]

With no ROOT and no --files, it lints every tracked *.md under the current directory.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

# --- present-state patterns -------------------------------------------------
# Each entry: (rule-id, compiled regex, human message). Kept conservative to limit false
# positives; the goal is to catch the obvious tells, not to police every sentence.
HISTORY = [
    ("history-temporal", re.compile(
        r"\b(previously|formerly|used to|in the past|originally|back when|no longer|"
        r"deprecated|legacy|migrated from|renamed from|ported from|as of \d{4})\b", re.I),
     "history phrasing — describe the current state, not how it got here"),
    ("history-versions", re.compile(
        r"\b(v\d+\s*(?:vs\.?|→|->)\s*v\d+|old\s+(?:vs\.?|versus)\s+new|"
        r"the\s+(?:old|new)\s+(?:way|version|implementation))\b", re.I),
     "version-progression phrasing — describe coexisting options, not a timeline"),
    ("rationale", re.compile(
        r"\b(we chose|chosen because|the reason (?:we|for|is)|decided to|"
        r"intentionally|by design we|trade[- ]?off|for historical reasons|"
        r"rather than the|instead of the old)\b", re.I),
     "rationale narrative — state the rule, not the argument for it"),
]

# Headings under which history/rationale/target text is legitimate.
EXEMPT_HEADING = re.compile(r"^#{1,6}\s+(changelog|cleanup[- ]notes|target|history)\b", re.I)
LINK_RE = re.compile(r"(?<!\!)\[[^\]]+\]\(([^)]+)\)")  # [text](target), not images
SENTENCE_RE = re.compile(r"[^.!?\n]{40,}?[.!?]")  # substantial sentences only


def iter_md_files(roots, explicit):
    if explicit:
        for f in explicit:
            p = Path(f)
            if p.suffix.lower() == ".md" and p.is_file():
                yield p
        return
    search = roots or [Path(".")]
    for root in search:
        root = Path(root)
        if root.is_file() and root.suffix.lower() == ".md":
            yield root
        else:
            for p in sorted(root.rglob("*.md")):
                if any(part in {".git", "node_modules"} for part in p.parts):
                    continue
                yield p


def read_lines_skip_code(path):
    """Yield (lineno, text, exempt) skipping fenced code blocks; exempt tracks heading sections."""
    in_fence = False
    exempt = False
    for i, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if raw.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if raw.lstrip().startswith("#"):
            exempt = bool(EXEMPT_HEADING.match(raw.strip()))
        yield i, raw, exempt


def check_present_state(path):
    findings = []
    if "archive" in str(path).lower():
        return findings
    for lineno, text, exempt in read_lines_skip_code(path):
        if exempt:
            continue
        for rule, rx, msg in HISTORY:
            m = rx.search(text)
            if m:
                findings.append((path, lineno, "error", rule, f"{msg} — '{m.group(0)}'"))
    return findings


def check_links(path):
    findings = []
    base = path.parent
    for lineno, text, _ in read_lines_skip_code(path):
        for m in LINK_RE.finditer(text):
            target = m.group(1).strip()
            if target.startswith(("http://", "https://", "mailto:", "#", "tel:")):
                continue
            target = target.split("#", 1)[0].strip()
            if not target:
                continue
            if not (base / target).exists() and not Path(target).exists():
                findings.append((path, lineno, "error", "broken-link",
                                 f"link target not found — '{target}'"))
    return findings


def collect_sentences(path, table):
    if "archive" in str(path).lower():
        return
    for lineno, text, exempt in read_lines_skip_code(path):
        if exempt:
            continue
        for m in SENTENCE_RE.finditer(text):
            norm = re.sub(r"\s+", " ", m.group(0).strip().lower())
            if len(norm.split()) >= 8:
                table[norm].append((path, lineno))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("roots", nargs="*", help="doc roots to scan (default: *.md under cwd)")
    ap.add_argument("--files", nargs="*", default=None, help="lint exactly these files (for hooks)")
    ap.add_argument("--strict", action="store_true", help="treat warnings (duplication) as failures")
    ap.add_argument("--quiet", action="store_true", help="only print findings, no summary")
    args = ap.parse_args(argv)

    files = list(iter_md_files([Path(r) for r in args.roots], args.files))
    if not files:
        if not args.quiet:
            print("doc_lint: no Markdown files to check")
        return 0

    findings = []
    sentences = defaultdict(list)
    for f in files:
        findings += check_present_state(f)
        findings += check_links(f)
        collect_sentences(f, sentences)

    # duplication across files → warnings
    for norm, locs in sentences.items():
        distinct = {p for p, _ in locs}
        if len(distinct) > 1:
            where = ", ".join(f"{p}:{ln}" for p, ln in locs)
            findings.append((locs[0][0], locs[0][1], "warning", "duplication",
                             f"same sentence in {len(distinct)} docs (link, don't restate) — {where}"))

    errors = [x for x in findings if x[2] == "error"]
    warnings = [x for x in findings if x[2] == "warning"]
    for path, lineno, sev, rule, msg in sorted(findings, key=lambda x: (str(x[0]), x[1])):
        print(f"{path}:{lineno}: {sev}: [{rule}] {msg}")

    if not args.quiet:
        print(f"\ndoc_lint: {len(errors)} error(s), {len(warnings)} warning(s) across {len(files)} file(s)")
    return 1 if errors or (args.strict and warnings) else 0


if __name__ == "__main__":
    sys.exit(main())
