# CLAUDE.md

Guidance for Claude Code when working in this repository.

punchlist is a file-based issue tracker: one markdown file per issue, in the project's own git repo, driven by a Python CLI (`punch`). The design and its reasons are in [docs/design.md](docs/design.md).

## Documentation map

| File | Purpose |
|---|---|
| [README.md](README.md) | Setup and usage |
| [ROADMAP.md](ROADMAP.md) | Planned work, in order. Temporary: once punchlist can import it, it is imported into punchlist's own issues and deleted |
| [docs/design.md](docs/design.md) | What was decided and why: storage layout, file format, ids, config, importer |
| ARCHITECTURE.md | *Not written yet* -- see below |

**Keep this map current.** Adding a doc means adding a row here and in README.

### When to write ARCHITECTURE.md

Write it once there is a second module worth explaining -- likely when the importer or a web UI lands beside the core. Until then the module docstrings are the architecture. It is a **map**, not an encyclopedia: the design principles (fail closed, stdlib only, files are the database, agents are the primary user), then one short paragraph per module saying what it owns and what it must not do, then pointers into `docs/` for anything longer. If a section grows past a screen, it moves to `docs/<topic>.md` and leaves a pointer behind.

## Layout

| Folder | Holds |
|---|---|
| `punchlist/` | The package. CLI entry point is `punchlist/cli.py`, exposed as `punch` (and `punchlist`) |
| `tests/` | pytest; fixtures build issue roots in `tmp_path`, never in the repo |
| `tests/fixtures/` | Small synthetic input files (e.g. ROADMAP shapes for the importer) |
| `docs/` | Durable design notes, one topic per file |
| `tools/` | Dev-only scripts, if any are ever needed |
| `temp/` | Scratch output; gitignored |

## Non-negotiables

1. **Write files with the editor.** Mutations go through `Edit` (or `Write` for a new file) -- never a heredoc, `cat >`, or `sed -i`. The working tree is CRLF under `core.autocrlf`; shell writes produce LF, truncate on failure, and turn a reviewable diff into a program to be read. Reads can use whatever shell command is cheapest, but `Read` a file before you `Edit` it.
2. **Parsers fail closed.** A malformed header, unknown enum value, or unclassifiable import line is an error naming the file and line. A wrong issue is worse than a refused one.
3. **Assertions are tests.** If a snippet checks something about the code, it belongs in `tests/`, not a throwaway script.
4. **The second time you want a script, add a flag.** If a command's output needs the same trimming twice, give the CLI a flag for it. This project *is* that kind of tool; hold it to the rule.

## Coding style

- `from __future__ import annotations`; annotate signatures and non-obvious locals.
- Imports: relative (`from .config import X`) between modules inside `punchlist/`; absolute (`from punchlist.config import X`) in `tests/` and in any tool or script meant to run standalone.
- ASCII-only source: `--` not em dashes, `->` not arrows.
- Stdlib only at runtime. A new runtime dependency needs the user's agreement.
- Fix lint findings at the root rather than suppressing them.
- CLI output: terse, one line per issue by default, since agents pay for every token. `--json` for structured output.
- Reconfigure stdout/stderr to UTF-8 at CLI startup. Issue text is UTF-8, and on Windows a piped or redirected stdout falls back to cp1252 and dies partway through the output.

## Verifying a change

```bash
python -m pytest tests/test_x.py     # while iterating
python -m pytest tests/              # before committing
python -m pylint punchlist
python -m pyright punchlist
```

## Working with collaborators

- Design before implementing anything architectural; agree on it, then build.
- Don't widen scope. Do the asked thing; offer extras in one line.
- Report test warnings and errors, always.
- Never emit a placeholder or no-op shell command.
- Keep prose dense: cite the reason in one sentence, no padding.
- Commit only when asked.
