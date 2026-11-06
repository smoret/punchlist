---
id: pl-1c2bk
title: test_history_diff fails intermittently
type: bug
status: closed
priority: low
related: pl-nmsgn
created: 2026-11-05
---

Failed once in four full-suite runs (2026-11-05): `punch history --diff` printed only the header lines, as if the second commit's change wasn't seen. Passes in isolation. Suspect git's racy-index check: the test rewrites the file and commits within the same second.
