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
- **Agents are the primary user.** CLI first, terse one-line-per-issue output, `--json` where structured output helps. The web board (`punch serve`) is for the human on the team, over the same files.
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
      pl-7k3qx/comments/    # one file per comment, beside the issue in its bucket
        20261019T141205Z-claude.md
```

- The root is found by walking up from the working directory to the nearest `punchlist.json`, the way git finds `.git`.
- Filenames are the id only; a title in the filename would turn every rename into a file move.
- **Buckets** keep every folder small. GitHub's web UI lists only the first 1,000 entries in a folder, and a tracker's issues outlive that quickly since closed issues stay on disk. The bucket is the first `fanout` characters of the id's random part -- not the prefix, which is constant and would put everything in one folder. With the default `fanout` of 1 there are 32 buckets, so a folder reaches 1,000 entries at about 32,000 issues.
- Buckets had to land before the first real import: moving files later would put a rename of every issue into history. Changing `fanout` on an existing project has the same cost, so `check` treats a file in the wrong bucket as an error rather than quietly following it.
- One function maps an id to its path (`Config.issue_path`); nothing else builds issue paths. Short-id lookup searches every bucket.
- Status is a field, not a folder, for the same reason.
- Closed issues stay on disk with `status: closed`; `list` and `search` show every other status by default.
- Files are UTF-8. By default they are written with the platform's line endings, so a Windows checkout under `core.autocrlf` sees no churn; a repository that fixes its endings on disk (`.gitattributes` `eol=lf`) sets `line_endings` to match (see Config). Reads accept either.

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

**Format versioning.** Files carry no format version. Two things can drift, and neither needs one yet: the header *syntax* has not changed, and when it first does, a version field arrives with it -- a file without one is v1, so no existing file has to change. The *schema* (fields, enums, categories) lives in `punchlist.json`, which is versioned by git alongside the issues, so the config that matched an old revision is the config at the same commit. Upgrading a tracker after a breaking change will be a `punch migrate` that rewrites current files in one reviewable commit; old revisions in history are never loaded as issues, so they never need upgrading.

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
  "fields": ["id", "title", "type", "status", "priority", "rank", "category", "parent", "depends-on", "related", "labels", "created"],
  "list_fields": ["depends-on", "related", "labels"],
  "types": ["epic", "feature", "task", "bug", "subtask"],
  "statuses": ["open", "closed"],
  "priorities": ["high", "medium", "low"],
  "categories": [],
  "check_references": false,
  "mention_pattern": "\\b{id}\\b",
  "line_endings": "platform"
}
```

- Every key is **known**: an unknown key (a typo, say) or a wrongly typed value is an error. Keys that existed from the start are required. Keys added later (`check_references`, `mention_pattern`, `line_endings`) are optional and take a documented default when absent, because a new required key would stop every existing tracker from loading the moment punchlist updates. `init` writes them all, so they're discoverable.
- `fanout` is the bucket width, from 1 to `id_length`: 1 gives 32 buckets, 2 gives 1,024. Choose it before the first issue; see Storage layout.
- `fields` sets the allowed header fields and their write order. It must include the required five; extra custom fields are allowed.
- The enumerated fields are `type`, `status`, `priority`, and `category`, validated against `types`, `statuses`, `priorities`, and `categories`. None of them may be a list field.
- `priorities` is ordered highest first, so sorting works.
- `rank` is an ordinary entry in `fields`, so a config written before it existed has no rank until `"rank"` is added there by hand; until then `set rank=` is refused as an unknown field. It may not be a list field.
- `statuses` must include `open` and `closed`, which the commands rely on: `new` writes `open`, and `closed` is what `close` writes, what satisfies a dependency, and the one status `list` hides by default. Statuses between them are workflow states (`progress`, `review`) and count as unfinished everywhere. Their order is the board's lane order (see Web UI).
- `categories` may be empty, in which case any `category:` value is an error.
- `line_endings` is `platform` (CRLF on Windows, LF elsewhere), `lf`, or `crlf`, and applies to every file punch writes: issues, comments, and `punchlist.json` itself. `punch init --line-endings lf` sets it, so even the first file is written in it. There is no "keep each file's existing endings" choice: a new issue or comment has no file to copy from, so it would still need one of these as a fallback, and a fixed choice already matches a repository's `.gitattributes`.

## Hierarchy

Epics and subtasks are expressed with `parent: <id>`. `subtask` exists as a type, but nothing requires a subtask to have a parent or forbids a task from having one -- the link carries the structure.

## Links

`depends-on: pl-aaaaa, pl-bbbbb` says an issue waits on others. Only the waiting issue stores the link; "what does pl-aaaaa block?" is derived by reading every issue, so there's one source of truth -- two files can't disagree -- and adding a link changes one file.

- A dependency is satisfied when its status is `closed`. Any other status blocks, and so does a dependency whose file is missing, since it can't be confirmed done.
- `punch list --ready` shows issues whose dependencies are all closed: what can be worked on now. `--blocked` shows those still waiting. Both also work on `search`, and combine with the other filters.
- `punch show --json` adds `blocks`, the derived reverse.
- `new --depends-on` and `set depends-on=a,b` resolve short ids, drop repeats, and refuse an unknown id, a self-dependency, or a loop (`dependency loop: pl-a -> pl-b -> pl-a`) before writing. `check` reports a dependency that doesn't exist, and each group of issues that depend on each other in a cycle once, at its lowest id.
- `depends-on` is an ordinary config field and must also be a list field. `init` includes it; a tracker made before it existed adds `"depends-on"` to both `fields` and `list_fields` by hand.

**Editing a list field** (`labels`, `depends-on`, `related`): `field=a,b` sets the *whole* list, consistent with every other field, so `=` with one id on an issue that already has links drops the rest. Because adding one item is the common case, `set` also takes `field+=a,b` (add, keeping the rest) and `field-=a` (remove just those). Adding an item already there or removing one that isn't changes nothing, so a retry is safe; `-=` matches a stored id exactly before resolving it, so a link to a deleted issue can still be removed. Whatever the operator, the result goes through the same checks as `=` (loops, self-links, unknown ids), and `set` prints what a list change removed -- `updated pl-x: related (removed pl-aaaaa)` -- so a mistaken replace shows up at once. One operator per field per call.

`related: pl-aaaaa, pl-bbbbb` links issues without either one waiting: two angles on one problem, or a follow-up filed out of a closed issue. It never affects `--ready` or `--blocked`. Unlike `depends-on` it is **stored in both issues**:

- Deriving the reverse means reading every issue on each `show`, a cost that grows with the tracker (closed issues stay on disk); a symmetric link has no direction to treat as the single source of truth anyway. Stored on both sides, plain `show` shows it from either end with no scan -- it's in each header.
- `set A related=B,C` writes A and adds A to B and C; dropping a link (`related=C`, or `related=`) removes it from the other side too. `new --related` links back the same way. Agents run one command per intent, so a separate "now set the other side" step is the one that gets dropped. Every file's new text is computed and validated before any is written, so a bad id or a corrupt partner stops the whole change.
- Short ids are resolved and repeats dropped; an unknown id or a link to itself is refused. Loops are fine.
- Closing an issue keeps its links, so a closed issue still names its follow-ups.
- `check` reports a related id with no file, an issue related to itself, and a one-sided link (A lists B, B doesn't list A) -- which only a hand edit or a merge that kept one file's side can produce. It's on by default: `check` reads every issue anyway.
- Like `depends-on`, it must be a list field; `init` includes it and an older tracker adds it by hand.

## Comments

`punch comment <id> --body "..."` adds a comment as its own file, `issues/<bucket>/<id>/comments/<time>-<author>.md`, so two people commenting at once never touch the same file and a comment is never a merge conflict.

```markdown
---
author: claude
created: 2026-10-19T14:12:05Z
---

Tried X; blocked on Y.
```

- Same header format as issues, but a fixed schema: `author` and `created`, both required, nothing else. Config `fields` don't apply; a comment isn't an issue.
- `created` is UTC to the second, so order is right across machines and time zones. The filename is that time, compact (`20261019T141205Z`), then a slug of the author (`Steve Moret` -> `steve-moret`), so filenames sort by time. The same author twice in one second gets `-2`, `-3`, ...; files are created exclusively and never overwritten.
- **Author:** `--author`, else `$PUNCH_AUTHOR`, else git's `user.name`, else an error. Agents run under a human's git identity, so an agent should pass `--author` or set `PUNCH_AUTHOR`, or its comments are signed as that human.
- `punch show <id> --comments` prints them after the issue, oldest first; `--json` adds `comments: [{author, created, body}]`. Plain `show` stays exactly the issue file.
- `check` requires every folder beside an issue file to belong to an existing issue in that bucket, and every entry of a `comments/` folder to be a comment whose header parses and matches its filename.
- `punch close <id> --comment "why"` closes with a reason in one step. The comment is written first, so a failure never leaves an issue closed without its reason; closing an already-closed issue adds nothing, so a retried close doesn't repeat the comment.
- Editing or deleting a comment is a plain file edit.

## History

`punch history <id>` answers "what happened to this issue?" from git, so nothing new is stored or kept in sync. It lists, oldest first, every commit that changed the issue's file or one of its comments, in any bucket, plus every commit whose message mentions the id:

```
pl-7k3qx closed task high - Retry should back off on timeouts
2026-03-02  3f2a1c0  created (task)                  Add retry issue
2026-03-03  66a0ea3  priority none -> high           pl-7k3qx: prioritize
2026-03-04  caf109c  referenced                      pl-7k3qx: first slice
2026-03-06  7cd070f  closed; comment added (claude)  Finish retries
```

- Each changed file is classified by diffing it before and after the commit: `created`, `closed`, `reopened`, `field` (old -> new; for a list field the whole list, printed as `+added -removed`), `body` (+N -M lines), `moved` (re-bucketed), `deleted`, `reformatted` (bytes changed, fields and body didn't), and `comment-added`/`-edited`/`-deleted`. A commit that touched neither file but whose message matches is `referenced`.
- **Old revisions are read leniently.** They record the past, not what is valid now, so only the shared header syntax is required: a field or enum value today's config dropped is still a change worth naming. A revision without the syntax is `malformed`, and the next readable one `repaired`. This is the one place punchlist doesn't fail closed; current files are still checked as strictly as before.
- **Which commits mention an issue is config**, `mention_pattern`: a regex in which `{id}` stands for the full id and `{short}` for the part after the prefix. The default, `\b{id}\b`, matches the id as a whole word anywhere in the message, whether a project leads subjects with it or uses a trailer; `fixes {short}` suits one that writes short ids.
- `--since` takes a `YYYY-MM-DD` date (commits authored on or after it) or a revision (commits after it: `<rev>..HEAD`), so an agent can re-check an issue it read before. `--json` gives one record per commit -- `id`, `sha`, `date`, `author`, `subject`, `events` -- each event its `kind`, and `field`/`old`/`new` for field and status changes.
- `--diff` prints each body change as a unified diff (one line of context) under its line; with `--json` it adds a `diff` array to `body` events.
- `--tree` adds every issue under it by `parent`, at any depth, as of today's links: the issues are listed depth first, and each timeline line names its issue after the sha. One commit that touched several of them is a line per issue. It is an epic's progress at a glance, and it costs the same three git calls as one issue.
- `punch changed <date|rev>` asks the reverse question over a range: which issues were `created`, `closed`, `reopened`, or `rescoped` (title or body changed), grouped in that order, a line per commit -- `date sha id title`. The standup and release-notes view. Other events (a priority, a comment) aren't a change of scope and don't appear. It takes `list`'s filters, applied to the issues as they are now, with every status by default; so an issue deleted since isn't listed. `--json` is an object of the four groups, each an array of the same records `history --json` gives. When git pairs one issue's deleted file with another's new one as a rename, they are reported as the two issues they are.
- Outside a git repository, or in a shallow clone, both refuse rather than print a short timeline. It costs a fixed number of git calls however long the history: two `git log`s and one `git cat-file --batch` for every file version.

## Web UI

`punch serve` is a kanban board for the human on a team of agents: the agents keep using the CLI, and the person sees and moves the same files in a browser. It was built for a pipeline whose pieces are hard to keep track of in a terminal, after Jira was judged too heavy (too many fields) and a claude.ai artifact board kept its cards outside the repo.

- **Lanes are `statuses`**, in config order. A tracker adds workflow states (`"statuses": ["open", "decision", "progress", "closed"]`) and gets a lane for each; nothing else configures the board. `closed` is collapsed until opened, since closed issues accumulate.
- **Cards** show the title, the body's first paragraph (clipped to two lines; hidden in `closed`), the id, type, priority, category, labels, comment count, and -- when a `depends-on` issue isn't closed -- "Waiting on" with its ids. A free-text wait ("the lead's call on blend width") is a custom header field such as `waiting-on`; the board edits it when the config lists it.
- **Order within a lane is `sort_key`**, the same as `list`: priority, then rank. Dragging a card sets its status and a `rank` between its new neighbors of the same priority, so one file changes; a drop among another priority's cards lands at the nearest place in its own, because changing a priority by dragging would be a surprise. When the card above has no rank (or the two neighbors tie), nothing fits between, so that priority's cards in the lane are renumbered 1, 2, 3, .... Ranks are computed as decimals, never floats, so they stay plain (`1.5`, `0.15`).
- **Every write goes through `ops`**, the code behind `new` and `set`, so the board can't write what `punch check` would reject. The page also edits title, type, priority, category, labels, and the body, and adds comments and new cards (into the lane they're added in). A body edit replaces only the text after the header (`ops.set_body`), so the header lines stay byte for byte; blank edges are trimmed as for `new --body`.
- **Concurrent edits.** Each request reads the files; there is no server-side state. The page polls a fingerprint of the issue files (path, size, mtime) every few seconds and redraws when it changes, so an agent's `punch set` shows up without a reload. A move or field edit carries the value the page saw; if the file has changed since, the server answers 409 and writes nothing, and the page redraws instead of overwriting the agent. An unsaved description survives a redraw; after a refused save the page says so and keeps the text, and a second save replaces the other version -- a choice the person makes knowingly.
- **No git.** The board writes files, like the CLI, and never commits: issues may live in a folder that isn't a repository yet. Committing stays with whoever finishes the work, in the same commit as the work.
- **Local only.** It binds 127.0.0.1 and has no accounts. Shared issues are shared through the repository: each person clones it and runs their own `punch serve`. It refuses a request naming another host (DNS rebinding) and a write without a JSON content type (a cross-site form), so a web page can't drive it.
- **The author** of the board's comments is resolved at startup (`--author`, `$PUNCH_AUTHOR`, git's `user.name`), so a board that couldn't sign a comment refuses to start rather than failing at the first one.
- **Stdlib only.** `http.server` and one static HTML file with plain JavaScript; no framework, no build step, nothing fetched from the network.

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
