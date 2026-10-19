"""The issue file format: a front-matter header of `key: value` lines, then a markdown body.

    ---
    id: pl-7k3qx
    title: "Retry" should back off on timeouts
    type: task
    status: open
    created: 2026-10-09
    ---

    Body.

This is deliberately not YAML. The header is the lines between the first two
`---` lines; on each, the key is everything before the first `: ` and the value
is everything after it, verbatim -- no quoting or escaping, ever. Every value is
a string; fields the config lists in `list_fields` are split on `,` and
stripped. One blank line separates the header from the body and is not part of
the body.

Parsing fails closed: an unknown or repeated field, an empty value, an enum
value the config doesn't allow, or a missing required field is a PunchError
naming the file and line. Writing puts fields in config order, so a file this
module wrote round-trips byte-for-byte.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import REQUIRED_FIELDS, Config
from .errors import PunchError

DELIMITER = "---"
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

FieldValue = str | list[str]


@dataclass
class Issue:
    """One issue: its header fields, in no particular order, and its body text."""

    fields: dict[str, FieldValue]
    body: str = ""
    path: Path | None = field(default=None, compare=False)

    def text(self, name: str) -> str | None:
        """A scalar field's value, or None if absent."""
        value: FieldValue | None = self.fields.get(name)
        if isinstance(value, list):
            raise TypeError(f"{name} is a list field")
        return value

    def items(self, name: str) -> list[str]:
        """A list field's values; empty if absent."""
        value: FieldValue | None = self.fields.get(name)
        if isinstance(value, str):
            raise TypeError(f"{name} is not a list field")
        return [] if value is None else value

    @property
    def id(self) -> str:
        """The issue id, e.g. `pl-7k3qx`."""
        return self._required("id")

    @property
    def title(self) -> str:
        """The one-line title."""
        return self._required("title")

    @property
    def status(self) -> str:
        """The status, one of the config's `statuses`."""
        return self._required("status")

    def _required(self, name: str) -> str:
        value: str | None = self.text(name)
        if value is None:
            raise KeyError(name)
        return value


def parse_issue(text: str, config: Config, path: Path) -> Issue:
    """Parse an issue file's contents. `path` locates errors and is kept on the issue."""
    lines: list[str] = text.split("\n")
    if lines[0] != DELIMITER:
        raise PunchError(f"first line must be {DELIMITER!r}", path, 1)
    try:
        close: int = lines.index(DELIMITER, 1)
    except ValueError:
        raise PunchError(f"header has no closing {DELIMITER!r}", path, 1) from None

    fields: dict[str, FieldValue] = {}
    for index in range(1, close):
        lineno: int = index + 1
        name, value = _parse_line(lines[index], config, path, lineno)
        if name in fields:
            raise PunchError(f"duplicate field {name!r}", path, lineno)
        fields[name] = value

    missing: list[str] = [f for f in REQUIRED_FIELDS if f not in fields]
    if missing:
        raise PunchError(f"missing required field(s): {', '.join(missing)}", path, close + 1)

    body: str = "\n".join(lines[close + 1:])
    if body.startswith("\n"):
        body = body[1:]
    return Issue(fields, body, path)


def _parse_line(line: str, config: Config, path: Path, lineno: int) -> tuple[str, FieldValue]:
    name, sep, value = line.partition(": ")
    if not sep:
        raise PunchError(f"expected 'field: value', got {line!r}", path, lineno)
    if name not in config.fields:
        raise PunchError(f"unknown field {name!r}", path, lineno)
    if not value.strip():
        raise PunchError(f"empty value for {name!r}; omit the field instead", path, lineno)

    if name in config.list_fields:
        items: list[str] = [item.strip() for item in value.split(",")]
        if not all(items):
            raise PunchError(f"empty item in list field {name!r}", path, lineno)
        return name, items

    allowed: tuple[str, ...] | None = config.allowed(name)
    if allowed is not None and value not in allowed:
        choices: str = ", ".join(allowed) if allowed else "none configured"
        raise PunchError(f"{name} {value!r} is not one of: {choices}", path, lineno)
    if name == "created":
        _check_date(value, path, lineno)
    return name, value


def _check_date(value: str, path: Path, lineno: int) -> None:
    if DATE_RE.fullmatch(value):
        try:
            datetime.date.fromisoformat(value)
            return
        except ValueError:
            pass
    raise PunchError(f"created {value!r} is not a YYYY-MM-DD date", path, lineno)


def format_issue(issue: Issue, config: Config) -> str:
    """Serialize an issue, fields in config order. Inverse of parse_issue."""
    unknown: list[str] = [name for name in issue.fields if name not in config.fields]
    if unknown:
        raise PunchError(f"unknown field(s): {', '.join(unknown)}", issue.path)
    lines: list[str] = [DELIMITER]
    for name in config.fields:
        value: FieldValue | None = issue.fields.get(name)
        if value is None:
            continue
        if isinstance(value, list):
            if not value:
                continue
            value = ", ".join(value)
        if "\n" in value:
            raise PunchError(f"{name} must be a single line", issue.path)
        lines.append(f"{name}: {value}")
    lines.append(DELIMITER)
    text: str = "\n".join(lines) + "\n"
    return text + "\n" + issue.body if issue.body else text


def read_issue(path: Path, config: Config) -> Issue:
    """Read and parse one issue file."""
    return parse_issue(path.read_text(encoding="utf-8"), config, path)


def write_issue(issue: Issue, config: Config) -> Path:
    """Write an issue to `<issues_dir>/<id>.md`, in the platform's line endings.

    The text is parsed back before writing, so this never writes a file that
    `punch check` would reject.
    """
    path: Path = config.issues_path / f"{issue.id}.md"
    text: str = format_issue(issue, config)
    parse_issue(text, config, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    issue.path = path
    return path
