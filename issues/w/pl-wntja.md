---
id: pl-wntja
title: Board and list: show and sort by created and updated times
type: feature
status: closed
priority: medium
labels: board
created: 2026-11-08
---

## Use

On a long board in `punch serve`, finding the newest issues meant opening cards one at a time: cards don't show a date, and lanes have only one order (priority, then rank). That's the query `docs/design.md` says to wait for before adding a date ("Add one only when a query needs it"). This is that query.

## Proposal

1. **Dates on cards.** Each card shows when the issue was created and when it last changed, in short relative form ("3d", "5w"), with the full time in a tooltip.
2. **A board sort control:** `Rank` (today's order, the default), `Newest`, and `Recently updated`. The choice is a per-viewer setting kept in the browser. While a time order is on, dragging between lanes still changes status, but reordering within a lane is off, since a drop there can't mean a rank.
3. **`punch list --sort rank|created|updated`** (and `search`), sharing the same sort keys, so the CLI can answer the same question. `--json` gains `updated`.

## Where the times come from

Derived, not stored: no new header field.

- **One `git log` pass** over `issues_dir` (`--name-only`, commit time) gives each issue the time of the first commit that added its file (creation, to the second) and the time of the last commit that touched its file or its comments (update). The board computes this once per `version` change, not per request.
- **Uncommitted changes** (`git status --porcelain` under `issues_dir`) use the file's mtime instead, so a card the board just edited, or an issue not yet committed, counts as newest. mtime alone isn't usable: a clone or checkout resets it.
- **No git** (or no history yet): created falls back to the header's `created` date, and updated to mtime. `Newest` still works; `Recently updated` is approximate and the control says so.
- `Newest` sorts by header `created` first, so a hand-backdated or imported issue keeps its stated date. The git time only breaks ties within a day, which matters because ids are random and the date is the only order there today.

## Alternative considered

A stored `updated` header field, rewritten by every punch write. It's simpler to read and needs no git, but every edit gains a diff line, hand edits leave it stale, and it duplicates what git already records. It stays rejected unless deriving from git turns out to be too slow on a large history.

## Done when

- Cards show created and updated times. The board sorts by rank, newest, or recently updated.
- `punch list --sort` accepts the same three orders.
- Tests cover the git derivation, including uncommitted and no-history repos, and both sort orders. `docs/design.md` records where the times come from.
