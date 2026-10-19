# Architecture

A map, not an encyclopedia: the principles, then one paragraph per module saying what it owns and what it must not do. What was decided about the file format, ids, config, and the importer, and why, is in [docs/design.md](docs/design.md). If a section here grows past a screen, it moves to `docs/<topic>.md` and leaves a pointer behind.

## Principles

- **Fail closed.** A malformed file, an unknown enum value, or an import line that fits no shape is an error naming the file and line. A wrong issue is worse than a refused one, so nothing is skipped, guessed, or defaulted.
- **Files are the database.** No index, cache, or state beside the issue files and `punchlist.json`. Every command reads the files, so a hand edit or a `git checkout` is always visible.
- **Agents are the primary user.** Output is terse, one line per issue; `--json` where structure helps; errors are one line with a `path:line:` an agent can jump to.
- **Stdlib only at runtime.** Python >= 3.11.
- **Every write is validated first.** No code path writes a file that `punch check` would reject.

## Layers

Imports run one way, down this list; nothing imports `cli`.

```
cli          argv, cwd, stdin/stdout -- the only module that touches them
check        importer
ids          issue
config
errors
```

## Modules

**`errors.py`** owns `PunchError`, the one exception the CLI reports. It carries an optional `path` and `line`, and `describe(base)` renders `path:line: message` with the path relative to `base`, in forward slashes. Anything else escaping a command is a bug, not a user error, so nothing should catch broad exceptions to convert them.

**`config.py`** owns `punchlist.json`: finding the root (the nearest directory at or above a given start that has one, like `.git`), validating it, and writing it (`init_config`, `add_categories`). Every key is required and unknown keys are errors. It must not fill in defaults for a missing key -- `default_config` is only what `init` writes. It also owns which fields are enumerated (`ENUM_FIELDS`) and the shared "not one of" message (`Config.choice_error`), so the parser and the CLI word that error the same way.

**`issue.py`** owns the issue file format and every write to an issue file. `parse_issue` is the single definition of what a valid issue is; `format_issue` is its inverse, writing fields in config order so files punchlist writes round-trip byte for byte. `write_issue` parses its own output before writing, and with `create=True` refuses to overwrite (the guard against two `new`s drawing the same id). `replace_field`/`update_field` change one header line and keep every other byte, so `close` is a one-line diff even on a hand-edited file. Reads go through `read_text`, which turns invalid UTF-8 into a `PunchError` with a line and normalizes line endings; writes use the platform's line endings, matching a `core.autocrlf` checkout. A parsed `Issue` keeps each field's line number for later checks. It must not know about the CLI or print.

**`ids.py`** owns id generation and lookup. `new_id` draws Crockford base32 from `secrets` and redraws while a file (or an id planned in the same batch) exists, giving up after a bounded number of draws rather than looping. `resolve_id` turns a unique start of an id, with or without the `<prefix>-`, into a full id, consulting **filenames only** -- it must not parse issues, so one corrupt file can't block `show` or `close` of another.

**`check.py`** owns cross-file validation: the first parse error per file, `id:` matching the filename (which is also what keeps ids unique), parents that exist, and parent loops (reported once, at the loop's lowest id). It reports every problem rather than stopping, and must not modify anything. Parents are checked against filenames, not parsed issues, so a corrupt epic is one problem rather than one per child.

**`importer.py`** owns turning a markdown checklist into planned issues. `plan_import` is pure: text in, `ImportPlan` out (issues with ids, a note count, unclassified line ranges, categories the config lacks). It must not write anything; the CLI decides whether to refuse, add categories, and write. Its parser follows markdown's list-item rule -- a continuation belongs to the deepest open bullet whose content column it reaches -- and every line either lands somewhere or is reported. The shapes and the title and body rules are in [docs/design.md](docs/design.md#importing-a-markdown-roadmap).

**`cli.py`** owns argument parsing, the working directory, stdin and stdout, and exit codes; it is the only module that prints or calls `Path.cwd()`. Each subcommand is a `cmd_<name>(args) -> int`. It validates flag values before writing so an error names the flag rather than a file that was never written, and it is where user-facing wording lives: hints read like other tools ("run `punch check` to find corrupt files"). Commands that read every issue stop at the first bad file and point at `punch check`; `check` itself reports them all.

## Conventions across modules

- **Errors:** raise `PunchError(message, path, line)` from wherever the problem is found; only `cli.main` renders it, relative to the working directory.
- **JSON output:** header fields in config order (`_json_fields`), list fields as arrays, `ensure_ascii=False`, one line.
- **Tests:** `tests/` builds every root in `tmp_path`; synthetic import inputs live in `tests/fixtures/`. Tests and standalone scripts import `punchlist.x` absolutely; modules inside the package import each other relatively.
