---
id: pl-xn3p7
title: show --json reads every issue to derive blocks
type: task
status: open
priority: low
created: 2026-10-22
---

With a `depends-on` field, `punch show <id> --json` adds `blocks`: the issues that list this one in their `depends-on`. Only the waiting issue stores the link, so finding them means reading every issue in the tracker, open and closed, on every `show --json`.

Two costs:

- **Time grows with the tracker.** A one-issue read becomes an all-issue read, and closed issues stay on disk forever.
- **A corrupt file anywhere fails the command** (with the `punch check` hint), where reading one issue wouldn't.

Plain `show` doesn't pay either cost; only `--json` does. `--ready`/`--blocked` also read every issue, but `list` and `search` read everything anyway.

Options, when it matters:

- Drop `blocks` from `show --json`, or put it behind a flag (`--blocks`), so the default `show` stays a one-file read.
- Store the reverse as well, as proposed for `related` (pl-c9h1s); this gives up `depends-on`'s single source of truth, so `check` would need to verify the pairs.
- Keep it, if trackers stay small enough that nobody notices.

Wait for a real report of slowness or a failure before changing anything.
