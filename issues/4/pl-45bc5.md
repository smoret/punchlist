---
id: pl-45bc5
title: Board: edit parent, depends-on, and related links
type: feature
status: open
priority: medium
related: pl-yrwpf
created: 2026-11-05
---

The board's detail panel shows an issue's links (parent, depends on, blocks, related) as clickable ids but can't change them, so recording that one card waits on another, or moving a card under an epic, means leaving the board.

Workaround for now: `punch set <id> depends-on+=<other>`, `punch set <id> related+=<other>`, `punch set <id> parent=<epic>` (and `-=` to remove a link).

The server side already exists: `PATCH /api/issues/<id>` with `set` goes through `ops.set_fields`, which resolves short ids and refuses dependency and parent loops, self-links, and unknown ids, and writes both sides of a related link. What's missing is the page:
- add a link by searching issues by title or id, not by typing a full id;
- remove one link without retyping the rest (send the whole new list with the old one as `expect`, so a concurrent change is a 409, not a lost link);
- show a refused change (a loop, say) as the server's message.
`blocks` stays read-only: it is derived from other issues' depends-on.
