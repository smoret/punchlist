---
id: pl-8n2dh
title: punch search --context N: show the matching lines
type: feature
status: closed
created: 2026-10-19
---

Requested from a downstream tracker (2026-10-19). A search hit prints only the header line, so when the pattern matches deep in a long body the caller cannot tell *why* it matched without a `punch show` per hit -- a two-word search on that tracker returns three issues and needs three shows to find the one that mattered. Its bodies average ~1.2 KB, so each avoided show is real tokens.

Proposed: `--context N` prints, under each hit, every matching body line plus N lines either side (0 = the matching lines alone), indented and prefixed with the line number in the file, so a caller can jump to it. Long lines may be cut around the match. A title-only match prints no snippet. `--json` gains a `matches` array of {line, text}. Precedent: that tracker's own lint script has a `--context N`, which exists for the same reason.
