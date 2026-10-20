---
id: pl-5e4yy
title: import-md dry run prints uncategorized counts as '-'
type: bug
status: open
priority: low
created: 2026-10-19
---

The dry run and real run of `punch import-md` print per-category counts; issues with no category show as `  -: 6`, which reads like a typo. Print `(none)` instead (e.g. `  (none): 6`). The `-` comes from `ImportPlan.counts`, which also feeds the type counts, so change the label where the category lines are printed in `cmd_import_md` and keep `list`'s `-` columns as they are. Found while importing this repo's own ROADMAP.md.
