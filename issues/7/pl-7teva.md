---
id: pl-7teva
title: Issue history: an issue's timeline from git, and what changed in a range
type: feature
status: open
created: 2026-10-25
---

"What happened to this issue?" has no answer in `punch` today. A person opening an issue they haven't seen in weeks wants a timeline: when it was filed, when its scope changed, which commits did the work, when it closed. An agent wants the same as data, plus "has this changed since I last read it?". The nearest thing is `git log --grep <id>`, which finds commits whose *message* names the id and misses the rest: scope edits made in an unrelated commit, a `related` or `depends-on` change made from the other issue, a close in a commit that didn't name it. And it lists commits, not what changed in the issue -- a status flip and a body losing three paragraphs look the same.

Everything needed is already in git: the issue file's own history. Requested: two commands over one shared reading of it, so nothing new has to be stored or kept in sync.

**The event classifier** (shared). For a commit that changed an issue's file, diff the file before and after and name what happened: `created`, `closed`, `reopened`, a field changed (with old -> new, and for a list field what was added and removed), `body +N -M`, `comment added`. A commit that only *mentions* the id in its message is `referenced` -- work done under the issue that never touched its file.

**`punch history <id>`** -- one issue's timeline, oldest first:

```
ab-k3x9q  Variable amounts in costs                          open
2026-03-02  3f2a1c0  created (feature, parent ab-p7m2w)
2026-03-05  66a0ea3  body +6 -2                     ab-k3x9q: the first slice
2026-03-09  caf109c  body +3 -4; related +ab-d4t8n  ab-k3x9q: the second slice
2026-03-09  7cd070f  referenced                     ab-d4t8n: work on a sibling
```

- `--diff`: each body change inline under its line.
- `--tree`: an epic and all its children in one timeline, each line naming its issue -- an epic's progress at a glance.
- `--since <date|rev>`: only what is newer; how an agent re-checks an issue it read before, and how a person asks "what happened this week".
- `--json`: the same events as records -- sha, date, author, subject, and per event its kind, field, old, new.

**`punch changed <since>`** -- the reverse question over a range of history: which issues were created, closed, reopened, or re-scoped since a date or rev, grouped by event with the commit that did it. The standup and release-notes view. Takes `list`'s filters (`--type`, `--category`, `--parent`, `--label`) and `--json`.

**Which commits count as a mention is configuration, not a convention.** A `punchlist.json` setting, defaulting to the full id as a whole word anywhere in the message, so it works for a project that leads subjects with the id, one that uses a trailer, and one that writes "fixes k3x9q" (a unique short id could be opt-in).

**Outside a git repo or in a shallow clone**, say so plainly rather than print an empty timeline. Renames inside `issues/` (a re-bucketed file) should be followed.

Why both in one request: they are one classifier with two views, and designing it once for both keeps their event names and JSON identical.
