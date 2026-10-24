---
id: pl-c9h1s
title: Related links: a non-blocking link between issues
type: feature
status: closed
related: pl-xn3p7, pl-r4z53
created: 2026-10-22
---

`depends-on` is the only link between issues, and it means "waits on". Two issues often relate without either one waiting: two angles on one gap (one teaches the system to see a case, the other lets a person work around it when it can't), or a follow-up filed out of an issue that is already closed. Today the only way to say so is an id written in the body, which `punch` can't resolve, check, or show from the other side.

Requested: a `related` list field, by analogy with `depends-on`:

- `new --related` and `set related=a,b` resolve short ids, drop repeats, and refuse an unknown id or a link to itself. A loop is fine: it doesn't block.
- Stored on one issue only, like `depends-on`, with the reverse derived. `show` (and `show --json`) lists the related issues from both directions, so linking from either end shows on both and adding a link changes one file.
- It never affects `--ready` or `--blocked`.
- `check` reports a related id with no file.
- `init` includes it in `fields` and `list_fields`; an existing tracker adds it by hand, as with `depends-on`.

Seen in practice: a tracker that set `related=` while filing two such issues got `related is not a field in the config`, and the link went into the body as prose.
