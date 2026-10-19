# Design

punchlist is a file-based issue tracker for a project worked on by one developer and a crew of agents. This note records what was decided and why; the module docstrings say how.

## Why it exists

A single `ROADMAP.md` stops scaling past a few hundred items: an agent has to load most of the file (tens of thousands of tokens) to find one entry, and nothing can count, filter, or validate it. A tracker lets tools search and filter instead of parsing one big file.

The deciding requirement: **issue changes are files in the project's own git repo, committed alongside the code they describe.** That ruled out the off-the-shelf options:

- GitHub Issues: state lives off-repo, so closing an issue can't ride in the same commit as the fix.
- beads, Backlog.md: Go/Node toolchains and foreign schemas.
- git-bug: the data isn't readable as plain files.
- Jira and similar: far more process than one developer needs.

## Principles

- **Small and incremental.** Build the next feature when a real use asks for it.
- **Agents are the primary user.** CLI first, terse one-line-per-issue output, `--json` where structured output helps. A web UI is a maybe-later.
- **Stdlib only at runtime.** Python >= 3.11. Dev dependencies: pytest, pylint, pyright.
- **Fail closed.** A malformed file is an error naming the file and line, never a silently skipped or half-read issue. A wrong issue is worse than a refused one.
- **Files are the database.** No index, cache, or sidecar state; every command reads the files.

## Storage layout

```
<project root>/
  punchlist.json            # config; its presence marks the root
  issues/                   # configurable via "issues_dir"
    pl-7k3qx.md             # one file per issue, named by id only
    pl-7k3qx/comments/      # planned: one file per comment
      20261018T1412-claude.md
```

- The root is found by walking up from the working directory to the nearest `punchlist.json`, the way git finds `.git`.
- Filenames are the id only; a title in the filename would turn every rename into a file move.
- Status is a field, not a folder, for the same reason.
- Closed issues stay on disk with `status: closed`; `list` shows open ones by default.
- Files are UTF-8, written with the platform's line endings so a Windows checkout under `core.autocrlf` sees no churn.

## Issue file format

```markdown
---
id: pl-7k3qx
title: "Retry" should back off on timeouts
type: task
status: open
priority: low
category: networking
parent: pl-2x9fa
labels: retries, deferred
created: 2026-10-09
---

Free-form markdown body. Any headings the author likes.
```

The header is **deliberately not YAML** -- no PyYAML, no quoting, no type guessing:

- The header is the lines between the first two `---` lines; the first line of the file must be `---`.
- One field per line. The key is everything before the first `: `; the value is everything after it, verbatim to end of line. No quoting or escaping, ever -- the title above, which starts with a quote, is written as-is.
- Every value is a string. Fields the config lists in `list_fields` are split on `,` and stripped.
- One blank line separates the header from the body and is not part of the body.
- Required: `id`, `title`, `type`, `status`, `created`. Everything else is optional; an absent field is omitted, never written empty.
- `created` is a `YYYY-MM-DD` date. There are no `updated` or `closed` dates; git history has them. Add one only when a query needs it.
- The title lives in the header, not as a `# heading` in the body, so listing reads only headers and the title has a single source.

Errors, each naming file and line: a line without `: `, an unknown field, a repeated field, an empty value or list item, an enum value the config doesn't allow, a bad date, a missing required field.

Fields are written in config order, so diffs stay minimal and a file punchlist wrote round-trips byte-for-byte. Every write parses its own output first, so punchlist never writes a file its own validation would reject.

## IDs

- `<prefix>-<n chars>`. The prefix (lowercase letters and digits) and length (default 5) come from config.
- Lowercase Crockford base32 (`0-9a-z` minus `i`, `l`, `o`, `u`), drawn with `secrets`.
- `new` redraws if the file already exists, so a collision within one checkout is impossible. The remaining risk is two unmerged branches minting the same id: at 5 chars (about 33M ids) that's roughly 0.0006% for 20 concurrent creations, and it surfaces loudly as a git add/add conflict.
- `check` validates that every header parses, each `id:` matches its filename (which makes ids unique), every `parent` exists, and no chain of parents loops. It reports every problem, one `path:line: message` per line, and exits 1 if there are any. Projects can run it as a test.

## Config (`punchlist.json`)

Everything enumerable is config, so new categories, types, or priorities need no code change. `punch init --prefix pl` writes:

```json
{
  "prefix": "pl",
  "id_length": 5,
  "issues_dir": "issues",
  "fields": ["id", "title", "type", "status", "priority", "category", "parent", "labels", "created"],
  "list_fields": ["labels"],
  "types": ["epic", "feature", "task", "bug", "subtask"],
  "statuses": ["open", "closed"],
  "priorities": ["high", "medium", "low"],
  "categories": []
}
```

- Every key is required and unknown keys are errors.
- `fields` sets the allowed header fields and their write order. It must include the required five; extra custom fields are allowed.
- The enumerated fields are `type`, `status`, `priority`, and `category`, validated against `types`, `statuses`, `priorities`, and `categories`. None of them may be a list field.
- `priorities` is ordered highest first, so sorting works.
- `statuses` must include `open` and `closed`, which the commands rely on.
- `categories` may be empty, in which case any `category:` value is an error.

## Hierarchy

Epics and subtasks are expressed with `parent: <id>`. `subtask` exists as a type, but nothing requires a subtask to have a parent or forbids a task from having one -- the link carries the structure.

## Importing a markdown roadmap (planned)

`punch import-md <file>` converts an existing checklist-style `ROADMAP.md` into issues, writing into whichever root it discovers like every other command. It recognizes these shapes:

- `# Title` and the intro paragraph under it: not issues; skipped.
- `## Section` becomes the `category` (slugified); `### Subsection` becomes a label.
- `- [ ] **Title** -- body`: a task. The bold text is the title; the text after ` -- ` is the body.
- `- [ ] Plain text ...` with no bold: the title is the text before the first ` -- `, or the first sentence; the full text is the body.
- `- **Title** -- body` with nested children and no checkbox: an `epic`. Its children get `parent:` set to its id. Nesting goes up to three levels.
- Plain bullets with no checkbox or bold title inside a group ("Out of scope: ...") are notes, appended to the nearest parent's body.
- Relative links in bodies are rewritten from the source file's directory to the issues directory (`docs/x.md` -> `../docs/x.md`). Absolute URLs and `#anchor`-only links are left alone.

It fails closed: a line it can't classify is listed with its line number, and nothing is written until every line is accounted for or an explicit flag says to skip them. `--dry-run` prints counts by category and type plus any unclassified lines. Categories the import needs but the config lacks are listed; a real run refuses unless `--add-categories` writes them into `punchlist.json`.
