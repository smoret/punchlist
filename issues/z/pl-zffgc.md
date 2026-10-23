---
id: pl-zffgc
title: Search comment text
type: feature
status: open
priority: low
related: pl-7f2hq
created: 2026-10-19
---

`punch search` matches issue titles and bodies only. Comments added with `punch comment` often carry the newest findings ("tried X, blocked on Y"), so a search can miss the issue that matters.

Proposed: a search hit also counts when a comment on the issue matches, and the issue is listed once, as now.

Open questions:

- **Default or opt-in.** Searching comments by default changes what existing searches return for any tracker already relying on `search`; an opt-in flag (`--comments`) avoids that but is easy to forget. Decide which, or whether the default flips later.
- **`--context` snippets.** A match inside a comment has line numbers in the comment's file, not the issue's, so a snippet line must say which file it came from (for example a `comments/<file>:12:` prefix), and `--json` `matches` would need a `path` per entry.
- **Cost.** Each search would also read every comment file; fine at hundreds of issues, worth measuring at thousands.

Build when a real search misses something because it lives in a comment.
