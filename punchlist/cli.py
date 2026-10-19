"""The `punch` command line. Each subcommand is a `cmd_<name>(args) -> int` function.

Output is terse because agents pay for every token. Errors are one line on
stderr and exit status 1.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

from .config import (
    CONFIG_NAME, PREFIX_RE, Config, default_config, find_root, load_config, parse_config,
)
from .errors import PunchError
from .ids import new_id, resolve_id
from .issue import FieldValue, Issue, load_issues, parse_issue, replace_field, write_issue

# `list` filters on these scalar fields; each has a flag of the same name.
FILTER_FIELDS: tuple[str, ...] = ("status", "type", "category", "priority", "parent")


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
    root: Path = Path.cwd()
    path: Path = root / CONFIG_NAME
    if path.exists():
        raise PunchError("already exists", path)
    prefix: str = args.prefix
    if not PREFIX_RE.fullmatch(prefix):
        raise PunchError(f"prefix {prefix!r} must be lowercase letters and digits")
    data: dict[str, object] = default_config(prefix)
    parse_config(data, root, path)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
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
    issues: list[Issue] = _load_all(config)
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

    if args.json:
        print(json.dumps([_json_fields(issue, config) for issue in matches], ensure_ascii=False))
        return 0
    for issue in matches:
        columns: list[str] = [issue.id] + [
            issue.text(name) or "-" for name in ("status", "type", "priority", "category")
        ]
        print(" ".join(columns), issue.title)
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    """Print one issue's file, or with --json its fields plus `body`."""
    config: Config = _load()
    path: Path = config.issues_path / f"{resolve_id(config, args.id)}.md"
    text: str = path.read_text(encoding="utf-8")
    issue: Issue = parse_issue(text, config, path)
    if args.json:
        print(json.dumps({**_json_fields(issue, config), "body": issue.body}, ensure_ascii=False))
    else:
        sys.stdout.write(text)
    return 0


def cmd_close(args: argparse.Namespace) -> int:
    """Set an issue's status to closed, changing only that line."""
    config: Config = _load()
    path: Path = config.issues_path / f"{resolve_id(config, args.id)}.md"
    text: str = path.read_text(encoding="utf-8")
    issue: Issue = parse_issue(text, config, path)
    if issue.status == "closed":
        print(f"{issue.id} already closed")
        return 0
    path.write_text(replace_field(text, config, path, "status", "closed"), encoding="utf-8")
    print(f"closed {issue.id}")
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
    statuses = lst.add_mutually_exclusive_group()
    statuses.add_argument("--status", help="only this status (default: open)")
    statuses.add_argument("--all", action="store_true", help="every status")
    lst.add_argument("--type")
    lst.add_argument("--category")
    lst.add_argument("--priority")
    lst.add_argument("--parent", help="only children of this issue id")
    lst.add_argument("--label", action="append", help="repeatable; issues must have every label")
    lst.add_argument("--json", action="store_true", help="a JSON array of header fields")
    lst.set_defaults(func=cmd_list)

    show = sub.add_parser("show", help="print an issue")
    show.add_argument("id", help="an issue id, or any unique start of one")
    show.add_argument("--json", action="store_true", help="header fields plus body as JSON")
    show.set_defaults(func=cmd_show)

    close = sub.add_parser("close", help="set an issue's status to closed")
    close.add_argument("id", help="an issue id, or any unique start of one")
    close.set_defaults(func=cmd_close)

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
        print(f"punch: error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
