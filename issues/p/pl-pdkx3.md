---
id: pl-pdkx3
title: list and search default to every status but closed
type: feature
status: closed
priority: high
created: 2026-11-05
---

A tracker whose `statuses` add workflow states (`progress`, `decision`) loses those issues from the default `punch list`: `_select` defaults to `status == open`, so an issue moved to `progress` vanishes from the view agents read first. Default to every status except `closed` instead; `--status` and `--all` are unchanged. Needed by the board (lanes are statuses).
