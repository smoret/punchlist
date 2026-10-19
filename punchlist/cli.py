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
from .ids import new_id
from .issue import FieldValue, Issue, write_issue


def _load() -> Config:
    return load_config(find_root(Path.cwd()))


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
    for name in ("type", "priority", "category", "parent"):
        value: str | None = getattr(args, name)
        if value is not None:
            fields[name] = value
    # Checked before writing so the error names the flag, not a file never written.
    for name, value in fields.items():
        if name not in config.fields:
            raise PunchError(f"{name} is not a field in the config")
        error: str | None = config.choice_error(name, value)
        if error is not None:
            raise PunchError(error)
    parent: str | None = fields.get("parent")
    if parent is not None and not (config.issues_path / f"{parent}.md").is_file():
        raise PunchError(f"parent {parent!r} does not exist")
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
