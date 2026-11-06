---
id: pl-55yep
title: Request: configurable line endings for files punch writes
type: feature
status: closed
created: 2026-10-25
---

punch writes issue files (and `punch init` writes punchlist.json) in the platform's line
endings, so on Windows they are CRLF. A repository can standardize on LF everywhere, on disk
as well as in git, with `.gitattributes` `* text=auto eol=lf`. In such a repository every file
punch creates or edits arrives as CRLF: git warns "CRLF will be replaced by LF" on each
`git add`, and the working tree drifts into mixed line endings until files are checked out
again.

The need: let a project choose the line endings punch writes, through a punchlist.json
setting, so punch's output matches the repository's convention. Useful choices would be LF,
CRLF, and the current platform behavior, which should stay the default so existing projects
see no change. An option that keeps an edited file's existing line endings may also be worth
considering.

Requested by a downstream project, which uses LF on disk on Windows and seeded its first ten
issues with punch 0.3.0 (17dd79a).
