---
id: pl-grfaj
title: Rank: a fine-grained sort key beside priority
type: feature
status: closed
created: 2026-10-19
---

Priorities are a short ordered list, so items within one priority have no order, and an imported checklist loses the order its source file gave it.

Proposed: an optional `rank` header field holding any real number (`1`, `2`, `3.5`, `3.75`), so an item can be placed between two neighbors without renumbering.

- `new --rank`, `set rank=`.
- `list` and `search` sort by priority, then rank, then id, with unranked items last; `--json` includes it.
- `import-md` writes each item's position in the source file as its rank, so a migrated checklist keeps its order.
- `check` rejects a rank that isn't a number.

Measured motivation: a ~280-item checklist import where several groups say "in order" and the order is the only record of it.
