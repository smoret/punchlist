"""The `punch` command line. Each subcommand is a `cmd_<name>(args) -> int` function.

Output is terse because agents pay for every token. Errors are one line on
stderr and exit status 1.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path

from .check import check_issues, parent_loop
from .config import (
    CONFIG_NAME, REQUIRED_FIELDS, Config, add_categories, find_root, init_config, load_config,
)
from .errors import PunchError
from .ids import new_id, resolve_id
from .importer import ImportPlan, plan_import
from .issue import (
    FieldValue, Issue, format_issue, load_issues, parse_issue, read_issue, read_text,
    update_issue, write_issue,
)

# `list` filters on these scalar fields; each has a flag of the same name.
FILTER_FIELDS: tuple[str, ...] = ("status", "type", "category", "priority", "parent")


def _count(n: int, noun: str) -> str:
    """`1 issue`, `2 issues`."""
    return f"{n} {noun}{'' if n == 1 else 's'}"


def _load() -> Config:
    return load_config(find_root(Path.cwd()))


def _load_all(config: Config) -> list[Issue]:
    # load_issues stops at the first bad file; point at the command that reports them all.
    try:
        return load_issues(config)
    except PunchError as exc:
        raise PunchError(f"{exc.message} (run `punch check` to find corrupt files)",
                         exc.path, exc.line) from exc


def cmd_init(args: argparse.Namespace) -> int:
    """Write a default punchlist.json in the current directory."""
    path: Path = init_config(Path.cwd(), args.prefix)
    print(f"wrote {path}")
    return 0


def cmd_new(args: argparse.Namespace) -> int:
    """Create an issue and print its id."""
    config: Config = _load()
    title: str = args.title.strip()
    if not title:
        raise PunchError("title must not be empty")
    fields: dict[str, str] = {"title": title, "status": "open"}
    for name in ("type", "priority", "category"):
        value: str | None = getattr(args, name)
        if value is not None:
            fields[name] = value
    if args.parent is not None:
        fields["parent"] = resolve_id(config, args.parent)
    # Checked before writing so the error names the flag, not a file never written.
    for name, value in fields.items():
        if name not in config.fields:
            raise PunchError(f"{name} is not a field in the config")
        error: str | None = config.choice_error(name, value)
        if error is not None:
            raise PunchError(error)
    fields["created"] = datetime.date.today().isoformat()
    fields["id"] = new_id(config)
    issue_fields: dict[str, FieldValue] = dict(fields)
    if args.label:
        if "labels" not in config.list_fields:
            raise PunchError("labels is not a list field in the config")
        labels: list[str] = [item.strip() for label in args.label for item in label.split(",")]
        if not all(labels):
            raise PunchError("empty label")
        issue_fields["labels"] = labels
    issue = Issue(issue_fields, _read_body(args))
    write_issue(issue, config, create=True)
    print(issue.id)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    """Print matching issues, one line each: id status type priority category title."""
    config: Config = _load()
    _print_issues(config, _select(config, _load_all(config), args), args.json)
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    """Like `list`, but only issues whose title or body matches a regex."""
    config: Config = _load()
    try:
        pattern: re.Pattern[str] = re.compile(
            args.pattern, 0 if args.case_sensitive else re.IGNORECASE
        )
    except re.error as exc:
        raise PunchError(f"invalid regex {args.pattern!r}: {exc}") from None
    hits: list[Issue] = [
        issue for issue in _load_all(config)
        if pattern.search(issue.title) or pattern.search(issue.body)
    ]
    _print_issues(config, _select(config, hits, args), args.json)
    return 0


def _select(config: Config, issues: list[Issue], args: argparse.Namespace) -> list[Issue]:
    """The issues passing `list`'s filter flags, sorted by priority, created, then id."""
    wanted: dict[str, str] = {}
    for name in FILTER_FIELDS:
        value: str | None = getattr(args, name)
        if value is None:
            continue
        error: str | None = config.choice_error(name, value)
        if error is not None:
            raise PunchError(error)
        wanted[name] = resolve_id(config, value) if name == "parent" else value
    if "status" not in wanted and not args.all:
        wanted["status"] = "open"
    labels: list[str] = args.label or []
    if labels and "labels" not in config.list_fields:
        raise PunchError("labels is not a list field in the config")

    matches: list[Issue] = [
        issue for issue in issues
        if all(issue.text(name) == value for name, value in wanted.items())
        and all(label in issue.items("labels") for label in labels)
    ]
    rank: dict[str, int] = {p: i for i, p in enumerate(config.priorities)}
    matches.sort(key=lambda issue: (
        rank.get(issue.text("priority") or "", len(rank)), issue.text("created") or "", issue.id,
    ))
    return matches


def _print_issues(config: Config, issues: list[Issue], as_json: bool) -> None:
    """One line per issue (id status type priority category title), or a JSON array."""
    if as_json:
        print(json.dumps([_json_fields(issue, config) for issue in issues], ensure_ascii=False))
        return
    for issue in issues:
        columns: list[str] = [issue.id] + [
            issue.text(name) or "-" for name in ("status", "type", "priority", "category")
        ]
        print(" ".join(columns), issue.title)


def cmd_show(args: argparse.Namespace) -> int:
    """Print one issue's file, or with --json its fields plus `body`."""
    config: Config = _load()
    path: Path = config.issue_path(resolve_id(config, args.id))
    text: str = read_text(path)
    issue: Issue = parse_issue(text, config, path)
    if args.json:
        print(json.dumps({**_json_fields(issue, config), "body": issue.body}, ensure_ascii=False))
    else:
        sys.stdout.write(text)
    return 0


def cmd_close(args: argparse.Namespace) -> int:
    """Set an issue's status to closed, changing only that line."""
    config: Config = _load()
    path: Path = config.issue_path(resolve_id(config, args.id))
    issue: Issue = read_issue(path, config)
    if issue.status == "closed":
        print(f"{issue.id} already closed")
        return 0
    update_issue(path, config, {"status": "closed"})
    print(f"closed {issue.id}")
    return 0


def cmd_set(args: argparse.Namespace) -> int:
    """Set or remove header fields: `field=value`, or `field=` to remove an optional one."""
    config: Config = _load()
    issue_id: str = resolve_id(config, args.id)
    path: Path = config.issue_path(issue_id)
    issue: Issue = read_issue(path, config)
    # Everything is validated before writing, so an error names the argument.
    changes: dict[str, str | None] = {}
    for assignment in args.assignments:
        name, sep, raw = assignment.partition("=")
        name = name.strip()
        if not sep:
            raise PunchError(f"expected field=value, got {assignment!r}")
        if name in changes:
            raise PunchError(f"{name} is set twice")
        if name not in config.fields:
            raise PunchError(f"{name} is not a field in the config")
        if name == "id":
            raise PunchError("id cannot be changed; it names the file")
        changes[name] = _set_value(config, issue_id, name, raw.strip())

    changed: list[str] = [
        name for name, value in changes.items() if _field_text(issue, name) != value
    ]
    if not changed:
        print(f"{issue_id} unchanged")
        return 0
    update_issue(path, config, {name: changes[name] for name in changed})
    print(f"updated {issue_id}: {', '.join(changed)}")
    return 0


def _set_value(config: Config, issue_id: str, name: str, value: str) -> str | None:
    """The header text `punch set` writes for one assignment; None removes the field."""
    if not value:
        if name in REQUIRED_FIELDS:
            raise PunchError(f"{name} is required and cannot be removed")
        return None
    if name in config.list_fields:
        items: list[str] = [item.strip() for item in value.split(",")]
        if not all(items):
            raise PunchError(f"empty item in {name}")
        return ", ".join(items)
    error: str | None = config.choice_error(name, value)
    if error is not None:
        raise PunchError(error)
    if name == "parent":
        value = resolve_id(config, value)
        loop: list[str] | None = parent_loop(config, issue_id, value)
        if loop is not None:
            raise PunchError(f"parent loop: {' -> '.join(loop)}")
    return value


def _field_text(issue: Issue, name: str) -> str | None:
    """A field as its header text (list fields joined), or None if absent."""
    value: FieldValue | None = issue.fields.get(name)
    return ", ".join(value) if isinstance(value, list) else value


def cmd_check(_args: argparse.Namespace) -> int:
    """Print every problem in every issue file, one per line; exit 1 if there are any."""
    config: Config = _load()
    count, problems = check_issues(config)
    for problem in problems:
        print(problem.describe(Path.cwd().resolve()))
    if problems:
        return 1
    print(f"{_count(count, 'issue')} ok")
    return 0


def cmd_import_md(args: argparse.Namespace) -> int:
    """Import a markdown roadmap as issues; --dry-run reports what it would create."""
    config: Config = _load()
    source = Path(args.file)
    try:
        text: str = read_text(source)
    except OSError as exc:
        raise PunchError(f"cannot read: {exc.strerror}", source) from None
    plan: ImportPlan = plan_import(text, source, config, datetime.date.today().isoformat())

    verb: str = "would create" if args.dry_run else "creating"
    types: str = ", ".join(f"{n} {t}" for t, n in sorted(plan.counts("type").items()))
    print(f"{verb} {_count(len(plan.issues), 'issue')} ({types}); "
          f"{_count(plan.notes, 'note')} folded into parents")
    for category, count in sorted(plan.counts("category").items()):
        print(f"  {category}: {count}")
    shown: str = source.as_posix()
    if plan.unclassified:
        print("unclassified lines (move them, or pass --skip-unclassified):")
        for item in plan.unclassified:
            span: str = str(item.start) if item.start == item.end else f"{item.start}-{item.end}"
            print(f"  {shown}:{span}: {item.reason}")
    if plan.missing_categories:
        print(f"categories missing from {CONFIG_NAME} (add them with --add-categories): "
              + ", ".join(plan.missing_categories))

    blocked: bool = (bool(plan.unclassified) and not args.skip_unclassified) or (
        bool(plan.missing_categories) and not args.add_categories)
    if args.dry_run:
        return 1 if blocked else 0
    if blocked:
        raise PunchError("nothing imported")

    if plan.missing_categories:
        config = add_categories(config.root, plan.missing_categories)
    for issue in plan.issues:  # validate every issue before writing any
        parse_issue(format_issue(issue, config), config, config.issue_path(issue.id))
    for issue in plan.issues:
        write_issue(issue, config, create=True)
    print(f"created {_count(len(plan.issues), 'issue')}")
    return 0


def _json_fields(issue: Issue, config: Config) -> dict[str, FieldValue]:
    """An issue's header fields in config order, for --json output."""
    return {name: issue.fields[name] for name in config.fields if name in issue.fields}


def _read_body(args: argparse.Namespace) -> str:
    """The body from --body or --body-file (`-` for stdin), trimmed of blank edges."""
    text: str
    if args.body is not None:
        text = args.body
    elif args.body_file == "-":
        text = sys.stdin.read()
    elif args.body_file is not None:
        path = Path(args.body_file)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise PunchError(f"cannot read body file: {exc.strerror}", path) from None
    else:
        return ""
    text = text.replace("\r\n", "\n").strip("\n")
    return text + "\n" if text.strip() else ""


def _add_filters(parser: argparse.ArgumentParser) -> None:
    """The filter and output flags `list` and `search` share; `_select` reads them."""
    statuses = parser.add_mutually_exclusive_group()
    statuses.add_argument("--status", help="only this status (default: open)")
    statuses.add_argument("--all", action="store_true", help="every status")
    parser.add_argument("--type")
    parser.add_argument("--category")
    parser.add_argument("--priority")
    parser.add_argument("--parent", help="only children of this issue id")
    parser.add_argument("--label", action="append", help="repeatable; issues must have every label")
    parser.add_argument("--json", action="store_true", help="a JSON array of header fields")


def build_parser() -> argparse.ArgumentParser:
    """The argument parser for every subcommand."""
    parser = argparse.ArgumentParser(prog="punch", description="A file-based issue tracker.")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help=f"write a default {CONFIG_NAME} here")
    init.add_argument("--prefix", required=True, help="issue id prefix, e.g. pl for pl-7k3qx")
    init.set_defaults(func=cmd_init)

    new = sub.add_parser("new", help="create an issue; prints its id")
    new.add_argument("title")
    new.add_argument("--type", required=True)
    new.add_argument("--priority")
    new.add_argument("--category")
    new.add_argument("--parent", help="id of the parent issue")
    new.add_argument("--label", action="append", help="repeatable; commas also separate labels")
    body = new.add_mutually_exclusive_group()
    body.add_argument("--body", help="body text")
    body.add_argument("--body-file", help="read the body from a file, or `-` for stdin")
    new.set_defaults(func=cmd_new)

    lst = sub.add_parser(
        "list", help="list open issues: id status type priority category title"
    )
    _add_filters(lst)
    lst.set_defaults(func=cmd_list)

    search = sub.add_parser(
        "search", help="list open issues whose title or body matches a regex"
    )
    search.add_argument(
        "pattern",
        help=r"a Python regex, case-insensitive unless --case-sensitive; \bword\b for whole words",
    )
    search.add_argument("--case-sensitive", action="store_true")
    _add_filters(search)
    search.set_defaults(func=cmd_search)

    show = sub.add_parser("show", help="print an issue")
    show.add_argument("id", help="an issue id, or any unique start of one")
    show.add_argument("--json", action="store_true", help="header fields plus body as JSON")
    show.set_defaults(func=cmd_show)

    close = sub.add_parser("close", help="set an issue's status to closed")
    close.add_argument("id", help="an issue id, or any unique start of one")
    close.set_defaults(func=cmd_close)

    set_ = sub.add_parser("set", help="set header fields: field=value, or field= to remove")
    set_.add_argument("id", help="an issue id, or any unique start of one")
    set_.add_argument("assignments", nargs="+", metavar="field=value")
    set_.set_defaults(func=cmd_set)

    check = sub.add_parser("check", help="validate every issue file; exit 1 on any problem")
    check.set_defaults(func=cmd_check)

    imp = sub.add_parser("import-md", help="import a markdown roadmap's checklist as issues")
    imp.add_argument("file")
    imp.add_argument("--dry-run", action="store_true",
                     help="report what would be created; exit 1 if a real run would refuse")
    imp.add_argument("--add-categories", action="store_true",
                     help=f"add categories the import needs to {CONFIG_NAME}")
    imp.add_argument("--skip-unclassified", action="store_true",
                     help="import anyway, leaving out lines that fit no shape")
    imp.set_defaults(func=cmd_import_md)

    return parser


def _configure_stdio() -> None:
    # Issue text is UTF-8; on Windows, redirected stdio defaults to cp1252.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """Entry point for the `punch` and `punchlist` console scripts."""
    _configure_stdio()
    args: argparse.Namespace = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except PunchError as exc:
        print(f"punch: error: {exc.describe(Path.cwd().resolve())}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
