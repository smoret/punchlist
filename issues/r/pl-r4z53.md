---
id: pl-r4z53
title: set on a list field: say it replaces, and report the links it removes
type: feature
status: open
related: pl-c9h1s
created: 2026-10-24
---

`punch set ID related=X` replaces the whole list. That is consistent with every other field, and `_partner_edits` correctly unlinks the dropped partners. But the most common thing an agent wants is "add one link", and nothing says the value replaces the list: `set --help` shows only `field=value`, and the README's example (`related=9qp  # a non-blocking link, written into both issues`) reads like an add. When an issue already has links, the natural command silently drops them on *both* issues and prints nothing.

Seen in practice: an agent added one related link to an issue that had three, which unlinked the other three from both sides. It noticed only because it happened to `show` the issue afterward, and restored the links by hand.

Requested, smallest first:

- `set --help` and the README say that a list field's value is the whole list (`labels`, `depends-on`, `related`).
- `set` prints the ids it removed from a link field (for example `related: removed pl-aaaaa, pl-bbbbb`), so a mistake shows up at once.
- Optional: `field+=id` and `field-=id` for list fields, so adding a link needs no read-modify-write.
