# CLAUDE.md

Guidance for Claude Code when working in this repository.

punchlist is a file-based issue tracker: one markdown file per issue, in the project's own git repo, driven by a Python CLI (`punch`). The design and its reasons are in [docs/design.md](docs/design.md).

## Documentation map

| File | Purpose |
|---|---|
| [README.md](README.md) | Setup and usage |
| [issues/](issues/) | Planned work, tracked with punchlist itself -- see "Tracking work" below |
| [docs/design.md](docs/design.md) | What was decided and why: storage layout, file format, ids, config, importer |
| [ARCHITECTURE.md](ARCHITECTURE.md) | The module map: what each module owns and must not do. Read before adding a module or moving responsibility between them |

**Keep this map current.** Adding a doc means adding a row here and in README.

### Keeping ARCHITECTURE.md honest

It is a **map**, not an encyclopedia. A new module gets a paragraph (what it owns, what it must not do) and a place in the layer diagram; moving a responsibility updates both paragraphs in the same commit. If a section grows past a screen, it moves to `docs/<topic>.md` and leaves a pointer behind.

## Tracking work

This repo tracks its own work with `punch` (from `.venv`; config in `punchlist.json`, issues in `issues/`). Build the next item only when a real use asks for it.

- Find work with `punch list --ready` (open, nothing it depends on still open); read an issue with `punch show <id>`. When one issue must wait for another, record it: `punch set <id> depends-on+=<other>`. When two relate without waiting (a follow-up, another angle on the same problem), `punch set <id> related+=<other>` links both ends; don't write ids into the body as prose. Use `+=`/`-=` for links and labels: `=` replaces the whole list and would drop the existing ones.
- Close an issue with `punch close <id> --comment "what finished it" --author claude` **in the same commit** as the work that finishes it -- that is the point of keeping issues in the repo.
- New work found along the way becomes `punch new "Title" --type task --body "..."` rather than a TODO comment.
- Change header fields with `punch set <id> field=value`; edit an issue's body by hand with the editor. `punch check` validates both.
- Record progress, findings, or a decision on an issue with `punch comment <id> --author claude --body "..."`. Always pass `--author`: without it the comment falls back to git's user name and is signed as Steve.
- Read an issue with its comments: `punch show <id> --comments`.
- Issues and comments are committed and outlive the conversation that wrote them: write about the project, not the people. Say what was decided and why ("decided: writable from the start"), not who asked or who agreed; a user who matters becomes a role ("a first user", "a team tracking a pipeline"). The same goes for docs, examples, and test data.

## Layout

| Folder | Holds |
|---|---|
| `src/punchlist/` | The package (PyPA src layout: importable only once installed, so tests run against the install). CLI entry point is `src/punchlist/cli.py`, exposed as `punch` (and `punchlist`) |
| `tests/` | pytest; fixtures build issue roots in `tmp_path`, never in the repo. Shared non-fixture helpers are in `tests/helpers.py` |
| `tests/fixtures/` | Small synthetic input files (e.g. ROADMAP shapes for the importer) |
| `docs/` | Durable design notes, one topic per file |
| `.github/workflows/` | `ci.yml` runs the tests on Python 3.11-3.15 across Linux, Windows, and macOS, plus the checks in "Verifying a change", on every push to main and every pull request. `release.yml` publishes to PyPI; see "Versioning" |
| `tools/` | Dev-only scripts, if any are ever needed |
| `temp/` | Scratch output; gitignored |

## Non-negotiables

1. **Write files with the editor.** Mutations go through `Edit` (or `Write` for a new file) -- never a heredoc, `cat >`, or `sed -i`. The working tree is CRLF under `core.autocrlf`; shell writes produce LF, truncate on failure, and turn a reviewable diff into a program to be read. Reads can use whatever shell command is cheapest, but `Read` a file before you `Edit` it.
2. **Parsers fail closed.** A malformed header, unknown enum value, or unclassifiable import line is an error naming the file and line. A wrong issue is worse than a refused one.
3. **Assertions are tests.** If a snippet checks something about the code, it belongs in `tests/`, not a throwaway script.
4. **The second time you want a script, add a flag.** If a command's output needs the same trimming twice, give the CLI a flag for it. This project *is* that kind of tool; hold it to the rule.

## Coding style

- `from __future__ import annotations`; annotate signatures and non-obvious locals.
- Imports: relative (`from .config import X`) between modules inside `src/punchlist/`; absolute (`from punchlist.config import X`) in `tests/` and in any tool or script meant to run standalone.
- ASCII-only source: `--` not em dashes, `->` not arrows.
- Stdlib only at runtime. A new runtime dependency needs the user's agreement.
- Fix lint findings at the root rather than suppressing them.
- CLI output: terse, one line per issue by default, since agents pay for every token. `--json` for structured output.
- Reconfigure stdout/stderr to UTF-8 at CLI startup. Issue text is UTF-8, and on Windows a piped or redirected stdout falls back to cp1252 and dies partway through the output.

## Verifying a change

```bash
python -m pytest tests/test_x.py     # while iterating
python -m pytest tests/              # before committing
python -m pylint src/punchlist
python -m pyright src/punchlist
punch check                          # the repo's own issues
```

## Branches

Work bigger than a commit or two goes on a branch and reaches main through a pull request, so main stays releasable and CI runs on every OS before merging. Branch names take a prefix, so they group by kind instead of crowding one flat namespace:

| Prefix | For | Example |
|---|---|---|
| `feature/` | New behavior (a `feature` issue) | `feature/issue-times` |
| `fix/` | A bug fix (a `bug` issue) | `fix/serve-unread-body` |
| `chore/` | Docs, CI, dependencies, tooling: nothing that changes the package | `chore/branch-names` |
| `dev/<name>/` | One developer's scratch or long-lived work | `dev/alex/board-spike` |

- After the prefix, a short lowercase hyphenated name of the work, not the issue id: the id is in the issue file the branch closes.
- A branch never touches the version; see "Versioning".
- Delete a branch once it's merged.

## Versioning

**The version is the git tag; no file holds it.** `hatch-vcs` builds it at install time: a commit tagged `v0.7.0` is `0.7.0`, and the third commit after it is `0.7.1.dev3`. So no commit edits a version, branches never conflict over one, and every commit installs as newer than the last -- pip skips a reinstall when the version is unchanged, which is what a stored version made people remember to bump.

- **A release is a tag.** When main holds what should ship and CI is green, choose the version from what merged since the last tag (`punch changed v0.6.1` lists it), tag main, and push the tag: `git tag v0.7.0 && git push origin v0.7.0`. `release.yml` builds and uploads it to PyPI, and refuses a build whose version isn't the tag's.
- New feature or behavior change: minor (`0.6.1` -> `0.7.0`). Bug fixes only: patch (`0.6.1` -> `0.6.2`). Below 1.0 a breaking change is also a minor bump; say it breaks in the pull request and the commit that merges it.
- Running the workflow by hand (`gh workflow run release.yml`) uploads the current dev version to TestPyPI instead. PyPI never accepts the same version twice, so a fix after publishing needs a new tag.
- Read the version with `punch --version`, or `git describe --tags` in the repo. An installed copy only learns of a new tag when reinstalled, so reinstall after tagging to see it.

## Working with collaborators

- Design before implementing anything architectural; agree on it, then build.
- Don't widen scope. Do the asked thing; offer extras in one line.
- Report test warnings and errors, always.
- Never emit a placeholder or no-op shell command.
- Keep prose dense: cite the reason in one sentence, no padding.
- Commit only when asked.
