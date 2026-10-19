"""The `punch` command line. Each subcommand is a `cmd_<name>(args) -> int` function.

Output is terse because agents pay for every token. Errors are one line on
stderr and exit status 1.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import CONFIG_NAME, PREFIX_RE, default_config, parse_config
from .errors import PunchError


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


def build_parser() -> argparse.ArgumentParser:
    """The argument parser for every subcommand."""
    parser = argparse.ArgumentParser(prog="punch", description="A file-based issue tracker.")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help=f"write a default {CONFIG_NAME} here")
    init.add_argument("--prefix", required=True, help="issue id prefix, e.g. pl for pl-7k3qx")
    init.set_defaults(func=cmd_init)

    return parser


def _configure_stdio() -> None:
    # Issue text is UTF-8; a redirected stdout on Windows defaults to cp1252.
    for stream in (sys.stdout, sys.stderr):
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
