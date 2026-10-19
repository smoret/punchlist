# Roadmap

Planned work for punchlist, in order. **Delete an item when it lands.** This file is temporary: the last item imports it into punchlist itself.

## Milestone 1: usable

- [ ] **`punch close <id>`** -- sets `status: closed`.
- [ ] **`punch check`** -- every file parses, id matches filename, ids unique, enum values valid, `parent` points at an existing issue. Exit 1 on any problem.
- [ ] **`punch import-md <file>`** -- the roadmap importer described in [docs/design.md](docs/design.md). `--dry-run` first; fails closed on unclassifiable lines. Verified against synthetic fixtures and then a dry run of a real roadmap.

**Milestone check:** rehearse a real import: `punch init` a root under `temp/`, import a real roadmap into it with `--add-categories`, then `list`, `show`, `close`, and `check` behave.

## After the milestone (only when a real use asks for it)

- [ ] **Dogfood** -- `punch init` here, import this ROADMAP.md, delete it, update CLAUDE.md and README.
- [ ] **Edit fields** -- `punch set <id> field=value ...`, rather than hand-editing headers.
- [ ] **Comments** -- `punch comment <id>`, stored as `issues/<id>/comments/<timestamp>-<author>.md` in the same header format (`author`, `created`), so concurrent comments never touch the same file.
- [ ] **Search** -- `punch search <regex>` over titles and bodies.
- [ ] **Stats** -- counts by category, type, priority, and status.
- [ ] **Links** -- `blocks` / `depends-on` as list fields.
- [ ] **Web UI** -- read-only first; a Flask blueprint a host app could mount is one option, a standalone `punch serve` another. Decide when it's wanted.
- [ ] **ARCHITECTURE.md** -- when there is a second module worth explaining (see CLAUDE.md).
