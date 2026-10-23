---
id: pl-7f2hq
title: Comment counts in list
type: feature
status: open
priority: low
related: pl-zffgc
created: 2026-10-19
---

`punch list` doesn't show whether an issue has comments, so a reader can't tell which issues have discussion without a `show --comments` each.

Proposed: show each issue's comment count.

Open questions:

- **Line format.** The one-line output is `id status type priority category title`, and the first five columns are space-free so callers can split them. A count column changes that shape and may break a caller that parses it. Options: `--json` only (`"comments": 3`), a flag that adds the column, or a marker appended after the title.
- **Cost.** Counting means listing every issue's `comments/` folder on each `list`; cheap, but it is one more directory read per issue.

Build when a reader needs it; `--json` only is the safe first step.
