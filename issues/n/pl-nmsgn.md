---
id: pl-nmsgn
title: Publish to PyPI
type: feature
status: closed
priority: high
related: pl-1c2bk
created: 2026-11-05
---

Make punchlist installable with `pipx install punchlist` / `pip install punchlist`. The name is free on PyPI (checked 2026-11-05).

- [x] `punch --version`
- [x] Build backend: hatchling (less config than setuptools for a src layout; it picks up static/board.html without package-data)
- [x] `[project.urls]`, classifiers, keywords
- [x] README: absolute GitHub links (relative ones break on the PyPI page); user install section; git required on PATH
- [x] CI: tests on 3.11-3.14 for every push (requires-python >=3.11 is untested below 3.14)
- [x] Release workflow `release.yml`: build, then upload with trusted publishing on a `v*` tag; environments `pypi` and `testpypi`
- [x] Trial upload to TestPyPI, then a real 0.6.0 release
