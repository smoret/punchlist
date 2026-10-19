# punchlist

A small, file-based issue tracker for projects worked on by one developer and a crew of agents. Each issue is a markdown file with a short header, stored in the project's own git repo, so closing an issue lands in the same commit as the work that closed it. The `punch` CLI creates, lists, filters, and checks them.

Status: under construction. See [ROADMAP.md](ROADMAP.md).

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate            # Windows (bash); .venv\Scripts\activate in cmd
pip install -e ".[dev]"
```

## Usage (planned)

```bash
punch init --prefix pl            # write punchlist.json in the current directory
punch new "Title" --type task --category tooling
punch list                        # open issues, one per line
punch show pl-7k3qx
punch close pl-7k3qx
punch check                       # validate every issue file
```

## Issue format

```markdown
---
id: pl-7k3qx
title: An example issue
type: task
status: open
created: 2026-10-18
---

Markdown body.
```

The header is `key: value` lines, values taken verbatim -- no quoting, no YAML. See [docs/design.md](docs/design.md) for the full rules.

## Documentation

| File | Purpose |
|---|---|
| [CLAUDE.md](CLAUDE.md) | Conventions for agents working in this repo |
| [ROADMAP.md](ROADMAP.md) | Planned work |
| [docs/design.md](docs/design.md) | What was decided and why |
