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
    7/                      # bucket: the first `fanout` characters after the prefix
      pl-7k3qx.md           # one file per issue, named by id only
      pl-7k3qx/comments/    # planned: one file per comment, in the issue's bucket
        20261018T1412-claude.md
```

- The root is found by walking up from the working directory to the nearest `punchlist.json`, the way git finds `.git`.
- Filenames are the id only; a title in the filename would turn every rename into a file move.
- **Buckets** keep every folder small. GitHub's web UI lists only the first 1,000 entries in a folder, and a tracker's issues outlive that quickly since closed issues stay on disk. The bucket is the first `fanout` characters of the id's random part -- not the prefix, which is constant and would put everything in one folder. With the default `fanout` of 1 there are 32 buckets, so a folder reaches 1,000 entries at about 32,000 issues.
- Buckets had to land before the first real import: moving files later would put a rename of every issue into history. Changing `fanout` on an existing project has the same cost, so `check` treats a file in the wrong bucket as an error rather than quietly following it.
- One function maps an id to its path (`Config.issue_path`); nothing else builds issue paths. Short-id lookup searches every bucket.
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
- `rank` is an optional plain decimal (`2`, `-1`, `3.75`; no exponents, `nan`, or `inf`), kept as written. Priorities are a short list, so items within one priority have no order of their own; a rank gives them one, and a number can always fit between two neighbors (`2.5` between `2` and `3`) without renumbering.

`list` and `search` sort by priority (config order), then rank, then `created`, then id; an issue missing a priority or rank sorts after those that have one.
- The title lives in the header, not as a `# heading` in the body, so listing reads only headers and the title has a single source.

Errors, each naming file and line: a line without `: `, an unknown field, a repeated field, an empty value or list item, an enum value the config doesn't allow, a bad date, a missing required field.

Fields are written in config order, so diffs stay minimal and a file punchlist wrote round-trips byte-for-byte. Every write parses its own output first, so punchlist never writes a file its own validation would reject.

## IDs

- `<prefix>-<n chars>`. The prefix (lowercase letters and digits) and length (default 5) come from config.
- Lowercase Crockford base32 (`0-9a-z` minus `i`, `l`, `o`, `u`), drawn with `secrets`.
- `new` redraws if the file already exists, so a collision within one checkout is impossible. The remaining risk is two unmerged branches minting the same id: at 5 chars (about 33M ids) that's roughly 0.0006% for 20 concurrent creations, and it surfaces loudly as a git add/add conflict.
- `check` validates that every file is in its bucket, every header parses, each `id:` matches its filename (which, with buckets enforced, makes ids unique), every `parent` exists, and no chain of parents loops. It reports every problem, one `path:line: message` per line, and exits 1 if there are any. Projects can run it as a test.
- An opt-in **reference check** also flags ids mentioned in bodies ("blocked on pl-7k3qx") that name no issue. Only this tracker's own id shape counts -- `<prefix>-` plus `id_length` id-alphabet characters, on word boundaries -- so another tracker's ids are never flagged; a closed issue still exists. It is opt-in because a dangling mention is a policy question, not a malformed file: a body may quote an example id. Turn it on per tracker with `"check_references": true`, or for one run with `punch check --references`.

## Config (`punchlist.json`)

Everything enumerable is config, so new categories, types, or priorities need no code change. `punch init --prefix pl` writes:

```json
{
  "prefix": "pl",
  "id_length": 5,
  "fanout": 1,
  "issues_dir": "issues",
  "fields": ["id", "title", "type", "status", "priority", "rank", "category", "parent", "labels", "created"],
  "list_fields": ["labels"],
  "types": ["epic", "feature", "task", "bug", "subtask"],
  "statuses": ["open", "closed"],
  "priorities": ["high", "medium", "low"],
  "categories": [],
  "check_references": false
}
```

- Every key is **known**: an unknown key (a typo, say) or a wrongly typed value is an error. Keys that existed from the start are required. Keys added later (`check_references`) are optional and take a documented default when absent, because a new required key would stop every existing tracker from loading the moment punchlist updates. `init` writes them all, so they're discoverable.
- `fanout` is the bucket width, from 1 to `id_length`: 1 gives 32 buckets, 2 gives 1,024. Choose it before the first issue; see Storage layout.
- `fields` sets the allowed header fields and their write order. It must include the required five; extra custom fields are allowed.
- The enumerated fields are `type`, `status`, `priority`, and `category`, validated against `types`, `statuses`, `priorities`, and `categories`. None of them may be a list field.
- `priorities` is ordered highest first, so sorting works.
- `rank` is an ordinary entry in `fields`, so a config written before it existed has no rank until `"rank"` is added there by hand; until then `set rank=` is refused as an unknown field. It may not be a list field.
- `statuses` must include `open` and `closed`, which the commands rely on.
- `categories` may be empty, in which case any `category:` value is an error.

## Hierarchy

Epics and subtasks are expressed with `parent: <id>`. `subtask` exists as a type, but nothing requires a subtask to have a parent or forbids a task from having one -- the link carries the structure.

## Importing a markdown roadmap

`punch import-md <file>` converts an existing checklist-style `ROADMAP.md` into issues, writing into whichever root it discovers like every other command. It recognizes these shapes:

- `# Title` on the first line and the prose under it: the file's intro; skipped.
- `## Section` becomes the `category` (slugified); `### Subsection` becomes a label.
- `- [ ] ...`: a `task`, whether or not it has children.
- `- ...` with nested bullets and no checkbox: an `epic`.
- `- ...` with no checkbox and no children ("Out of scope: ...", "**Waits for its runner**: ..."): a note, folded into its parent's body as a bullet.
- An indented paragraph or table after a blank line belongs to the item it is indented under, as in markdown: two spaces past a bullet's column. It is added to that item's body.
- Nested bullets get `parent:` set to the nearest enclosing task or epic, at any depth.
- If the config has a `rank` field, each issue's rank is its position among the imported items (1, 2, 3, ...), so the source's order survives; a checklist's order is often the only record of what comes next. Without the field the order is dropped, and the report says so.

Titles and bodies:

- The title is the first `**bold**` span, minus a trailing `.` or `:`. Text after the bold (`(ref 7.16)`, `, each ...`) stays in the body.
- A plain item's title is the text before the first ` -- `, `: `, `; `, or sentence end. Over 100 characters, it is cut at a word boundary with `...`.
- The body is the item's full text verbatim, bold title included, then its continuations and notes in document order. Nothing in the item is lost; the title is repeated once.
- Relative links are rewritten from the source file's directory to the issue's bucket directory (`docs/x.md` -> `../../docs/x.md`). Absolute URLs, root-relative paths, and `#anchor`-only links are left alone.

It fails closed. Prose outside any item (a section's intro paragraphs, say), a checkbox other than `[ ]`, a note with no parent, a bold title with no closing `**`, an unexpected heading, and tab indentation are each reported with their lines, and a real run writes nothing while any remain. Prose under a `##` has no home in an issue tracker, so it is refused rather than dropped: move it (to `docs/`, say) or pass `--skip-unclassified`.

`--dry-run` prints counts by type and category, the unclassified lines, and the categories the config lacks, and exits 1 if a real run would refuse. A real run refuses missing categories unless `--add-categories` appends them to `punchlist.json`. It validates every issue before writing any. Running it twice imports everything twice.
