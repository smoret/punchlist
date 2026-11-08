---
id: pl-8ph22
title: serve: a refused request's unread body resets the connection on Windows
type: bug
status: open
priority: low
labels: board
created: 2026-11-08
---

test_http_refuses_cross_site_shapes fails now and then on Windows with ConnectionAbortedError (WinError 10053). web.py's _api refuses a non-JSON POST (415) before reading the request body. On Windows, closing a socket that still has unread data sends a reset, so the client can lose the 415 reply. The 413 (too large) path has the same shape.

Fix: drain up to MAX_BODY of the declared Content-Length before replying to a refused write, or send Connection: close and shut down writing before closing, so the reply arrives before the reset.
