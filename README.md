# punchlist

A small, file-based issue tracker for projects worked on by one developer and a crew of agents. Each issue is a markdown file with a short header, stored in the project's own git repo, so closing an issue lands in the same commit as the work that closed it. The `punch` CLI creates, lists, filters, and checks them.

Status: early, and tracking its own work: planned work is in [issues/](https://github.com/smoret/punchlist/tree/main/issues) (`punch list`).

## Install

Needs Python 3.11 or later. `punch history` and `punch changed` read the repo's log, so they also need `git` on the PATH.

```bash
pipx install punchlist            # or: pip install punchlist
```

## Development

```bash
python -m venv .venv
.venv/Scripts/activate            # Windows (bash); .venv\Scripts\activate in cmd
pip install -e ".[dev]"
```

## Usage

```bash
punch --version                   # the installed version
punch init --prefix pl            # write punchlist.json in the current directory
punch init --prefix pl --line-endings lf   # write every file LF (default: the platform's)
punch new "Title" --type task --category tooling   # prints the new id
punch new "Title" --type task --priority high --rank 2.5   # rank orders within a priority
punch new "Title" --type bug --body-file -         # body from stdin
punch list                        # every status but closed: id status type priority category title
punch list --all --label cli --json
punch list --ready                # open issues whose depends-on issues are all closed
punch set 7k3 depends-on+=2x9     # 7k3 also waits on 2x9; --blocked lists what's waiting
punch set 7k3 related+=9qp        # add a non-blocking link, written into both issues
punch set 7k3 related-=9qp        # remove one link, from both issues
punch search 'retry|backoff'      # title or body, case-insensitive; takes list's filters
punch search retry --context 1    # plus each hit's matching lines (file line numbers), 1 line either side
punch show pl-7k3qx                # or any unique start: pl-7k, 7k3
punch show 7k3 --json             # header fields plus body
punch close pl-7k3qx
punch close 7k3 --comment "Fixed by the retry change." --author claude
punch comment 7k3 --author claude --body "Tried X; blocked on Y."   # author: flag, $PUNCH_AUTHOR, git
punch show 7k3 --comments         # the issue, then its comments oldest first
punch history 7k3                 # from git: each commit that changed it or mentions it
punch history 7k3 --since 2026-03-01 --json   # or --since <rev>: only what's newer
punch history 2x9 --tree --diff   # an epic and everything under it; body changes as diffs
punch changed 2026-03-01 --type bug   # created/closed/reopened/rescoped since; takes list's filters
punch set 7k3 priority=high labels=cli,docs   # a list's = replaces the WHOLE list; field= removes
punch set 7k3 labels+=api rank=2               # += adds to a list, -= removes; one op per field per call
punch serve                       # kanban board at http://127.0.0.1:8314/, a lane per status
punch serve --author alex --port 0 --no-browser    # any free port; comments signed alex
punch check                       # validate every issue file
punch check --references          # also flag ids mentioned in bodies that don't exist
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

The header is `key: value` lines, values taken verbatim -- no quoting, no YAML. See [docs/design.md](https://github.com/smoret/punchlist/blob/main/docs/design.md) for the full rules.

## Documentation

Links are absolute so they also work on the PyPI page.

| File | Purpose |
|---|---|
| [CLAUDE.md](https://github.com/smoret/punchlist/blob/main/CLAUDE.md) | Conventions for agents working in this repo |
| [issues/](https://github.com/smoret/punchlist/tree/main/issues) | Planned work, as punchlist issues |
| [docs/design.md](https://github.com/smoret/punchlist/blob/main/docs/design.md) | What was decided and why |
| [ARCHITECTURE.md](https://github.com/smoret/punchlist/blob/main/ARCHITECTURE.md) | The module map |

## License

MIT; see [LICENSE](https://github.com/smoret/punchlist/blob/main/LICENSE).
