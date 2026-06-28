# Present-state scrub — pattern catalogue, exception, examples

Read this when running **Procedure B (updating)** in `SKILL.md`. It is the detailed execution of
the *Present-state only* rule: the patterns to strip, the one sanctioned home for history, and
worked before/after examples. The goal: after the pass, a reader unfamiliar with the project's
past cannot tell the doc was ever different.

## Patterns to remove

The list is non-exhaustive — use judgement; some matches are false positives (e.g. "as of" in a
current-state assertion about a date the system *uses*, not a historical timestamp).

### Phrase patterns (anywhere in prose)

| Pattern | Why it goes |
|---|---|
| `previously …`, `formerly …`, `used to …`, `it used to be that …` | Historical |
| `retired`, `deprecated`, `superseded`, `replaced by` | Historical |
| `legacy`, `predecessor`, `pre-<X> refactor`, `old format`, `new format` | Historical / temporal-comparison |
| `as of <date>`, `since <date>`, `landed <date>`, `migrated on <date>` | Historical timestamp |
| `v1`, `v2`, `version 1`, `the old version`, `the new version` | Temporal progression |
| `now reads from …`, `now writes to …`, `now does X` (implies it didn't before) | Historical |
| `migrated from …`, `extracted from …`, `merged from former …` | Historical |
| `intentionally simpler than X`, `the cost is`, `the trade-off is`, `worth it because`, `… that's negligible` (cost-benefit aside) | Rationale |
| `we chose / picked / went with …`, `we considered … but` | Rationale |

### Section / heading patterns

| Pattern | Action |
|---|---|
| `## Status` / `> **Status:** active (as of …)` banners | Delete the banner block |
| `## Legacy …` / `### Legacy …` | Delete the section |
| `## First … Result (YYYY-MM-DD)` / one-time event records | Delete the section |
| `## … Statistics (as of YYYY-MM-DD)` | Delete the section |
| `> Merged from former …`, `Last updated: YYYY-MM-DD` atop an appendix | Delete |
| Design-rationale paragraphs (why X over Y) | Delete, or rewrite as a plain description of X |

### Inline annotations

| Pattern | Action |
|---|---|
| `(legacy)`, `(old)`, `(deprecated)`, `(pre-refactor)` parentheticals | Delete the parenthetical |
| `// Legacy (v1): …` lines in code samples | Delete the legacy lines |

## The exception — Changelog / Cleanup notes

A doc MAY carry an explicit changelog. These are intentional historical records: **preserve and
extend**, don't strip. Recognise by heading: `## Cleanup notes`, `## Change Log` / `## Changelog`,
`## Release Notes`, or `## History` when explicitly the doc's canonical changelog.

Canonical shape — terse dated bullets, one per change:

```markdown
## Cleanup notes

> - **2026-03-09:** Removed component X — superseded by Y.
> - **2026-04-06:** Feature Z shipped.
```

When you scrub history from the body, append a dated bullet here describing what was scrubbed (if
relevant). If no such section exists and the user wants the history kept somewhere, ask before
adding one. **Never fabricate dates** — use only dates the doc already carries or the user
provides. Many projects keep history in `git log` and carry no in-doc changelog at all — respect
the project's convention.

## Sweep command

A quick grep to surface candidates (read each hit in context):

```bash
grep -nE -i "previously|formerly|retired|legacy|predecessor|as of 2[0-9]{3}|landed|migrated from|merged from former|intentionally simpler|the cost is|trade-off|status:.+(active|implemented|target)|v1|v2|version 1|version 2" <target.md>
```

Run it again after editing — it should come back clean except inside a Changelog section.

## Examples

### Phrase

**Before:** The service now reads config from `config/` instead of the repo root. Previously the
config lived at the root; we moved it after the refactor to keep one source of truth.

**After:** The service reads config from `config/`.

### Status banner

**Before:**
```markdown
#### Cache layer
> Status: **active** (as of 2026-05-12). The Redis cache landed here; replaces the retired
> in-process LRU.

The cache is read-through with a 60 s TTL: …
```
**After:**
```markdown
#### Cache layer
The cache is read-through with a 60 s TTL: …
```

### Historical subsection

**Before:** `#### First Load Test (2026-04-03)` followed by a one-time run log.
**After:** *section deleted* — current throughput limits are described in present-state form in
the performance section.

## Notes

- **Never strip from the working tree without the user seeing the diff.** Deletions of doc
  sections deserve a paper trail — show the diff (or `git diff --staged`) before commit.
- **Preserve audit records.** Dated `manifest.json`, run logs, and reports under `archive/` are
  immutable historical artefacts by design — out of scope.
- **Tone after the pass:** declarative, present tense, naming current files and commands.
