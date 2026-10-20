# punchlist

A small, file-based issue tracker for projects worked on by one developer and a crew of agents. Each issue is a markdown file with a short header, stored in the project's own git repo, so closing an issue lands in the same commit as the work that closed it. The `punch` CLI creates, lists, filters, and checks them.

Status: early, and tracking its own work: planned work is in [issues/](issues/) (`punch list`).

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate            # Windows (bash); .venv\Scripts\activate in cmd
pip install -e ".[dev]"
```

## Usage

```bash
punch init --prefix pl            # write punchlist.json in the current directory
punch new "Title" --type task --category tooling   # prints the new id
punch new "Title" --type task --priority high --rank 2.5   # rank orders within a priority
punch new "Title" --type bug --body-file -         # body from stdin
punch list                        # open issues: id status type priority category title
punch list --all --label cli --json
punch search 'retry|backoff'      # title or body, case-insensitive; takes list's filters
punch search retry --context 1    # plus each hit's matching lines (file line numbers), 1 line either side
punch show pl-7k3qx                # or any unique start: pl-7k, 7k3
punch show 7k3 --json             # header fields plus body
punch close pl-7k3qx
punch set 7k3 priority=high labels=cli,docs   # field= removes an optional field
punch check                       # validate every issue file
punch import-md ROADMAP.md --dry-run   # what a markdown checklist would become
punch import-md ROADMAP.md --add-categories
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
| [issues/](issues/) | Planned work, as punchlist issues |
| [docs/design.md](docs/design.md) | What was decided and why |
| [ARCHITECTURE.md](ARCHITECTURE.md) | The module map |
