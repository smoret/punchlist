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

from .config import REQUIRED_FIELDS, Config, write_file
from .errors import PunchError

DELIMITER = "---"
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
# Plain decimals only (-2, 3, 3.75): no exponents, nan, or inf, so a rank reads as written.
RANK_RE = re.compile(r"-?\d+(\.\d+)?")

FieldValue = str | list[str]


@dataclass
class Issue:
    """One issue: its header fields, in no particular order, and its body text.

    A parsed issue also knows its file and the line each field was on, so
    later checks (`punch check`) can point at the right line.
    """

    fields: dict[str, FieldValue]
    body: str = ""
    path: Path | None = field(default=None, compare=False)
    lines: dict[str, int] = field(default_factory=dict[str, int], compare=False)
    body_line: int = field(default=0, compare=False)  # file line of the body's first line

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

    @property
    def rank(self) -> float | None:
        """The optional `rank` sort key as a number; None if unranked."""
        value: str | None = self.text("rank")
        return None if value is None else float(value)

    def _required(self, name: str) -> str:
        value: str | None = self.text(name)
        if value is None:
            raise KeyError(name)
        return value


@dataclass(frozen=True)
class Header:
    """A file split at its front-matter delimiters, before any field is interpreted."""

    lines: list[tuple[int, str]]  # (1-based line number, raw text) between the delimiters
    close_line: int  # 1-based line number of the closing ---
    body: str
    body_line: int  # 1-based line number of the body's first line


def split_header(text: str, path: Path) -> Header:
    """Split a file in the shared header format (issues and comments) into header and body."""
    lines: list[str] = text.split("\n")
    if lines[0] != DELIMITER:
        raise PunchError(f"first line must be {DELIMITER!r}", path, 1)
    try:
        close: int = lines.index(DELIMITER, 1)
    except ValueError:
        raise PunchError(f"header has no closing {DELIMITER!r}", path, 1) from None
    body: str = "\n".join(lines[close + 1:])
    body_line: int = close + 2  # the line after the closing ---
    if body.startswith("\n"):
        body = body[1:]
        body_line += 1
    return Header([(i + 1, lines[i]) for i in range(1, close)], close + 1, body, body_line)


def parse_issue(text: str, config: Config, path: Path) -> Issue:
    """Parse an issue file's contents. `path` locates errors and is kept on the issue."""
    header: Header = split_header(text, path)
    fields: dict[str, FieldValue] = {}
    linenos: dict[str, int] = {}
    for lineno, line in header.lines:
        name, value = _parse_line(line, config, path, lineno)
        if name in fields:
            raise PunchError(f"duplicate field {name!r}", path, lineno)
        fields[name] = value
        linenos[name] = lineno

    missing: list[str] = [f for f in REQUIRED_FIELDS if f not in fields]
    if missing:
        raise PunchError(
            f"missing required field(s): {', '.join(missing)}", path, header.close_line
        )
    return Issue(fields, header.body, path, linenos, header.body_line)


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

    error: str | None = config.choice_error(name, value)
    if error is not None:
        raise PunchError(error, path, lineno)
    if name == "created":
        _check_date(value, path, lineno)
    if name == "rank":
        rank: str | None = rank_error(value)
        if rank is not None:
            raise PunchError(rank, path, lineno)
    return name, value


def rank_error(value: str) -> str | None:
    """Why `value` isn't a valid rank, or None if it is."""
    if RANK_RE.fullmatch(value):
        return None
    return f"rank {value!r} is not a number like 2 or 2.5"


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


def edit_header(text: str, config: Config, path: Path, changes: dict[str, str | None]) -> str:
    """`text` with header fields set (a string) or removed (None), every other byte kept.

    Editing lines in place, rather than re-serializing, keeps a hand-edited
    file's field order and makes the diff only the changed lines. An existing
    field changes on its own line; a new one goes after the last field that
    precedes it in config order. The result is parsed, so an invalid value or
    a removed required field is an error.
    """
    lines: list[str] = text.split("\n")
    try:
        close: int = lines.index(DELIMITER, 1)
    except ValueError:
        raise PunchError(f"header has no closing {DELIMITER!r}", path, 1) from None
    for name, value in changes.items():
        if value is not None and ("\n" in value or "\r" in value):
            raise PunchError(f"{name} must be a single line")
        index: int | None = next(
            (i for i in range(1, close) if lines[i].partition(": ")[0] == name), None
        )
        if value is None:
            if index is not None:
                del lines[index]
                close -= 1
        elif index is not None:
            lines[index] = f"{name}: {value}"
        else:
            rank: int = config.fields.index(name)
            at: int = 1
            for i in range(1, close):
                key: str = lines[i].partition(": ")[0]
                if key in config.fields and config.fields.index(key) < rank:
                    at = i + 1
            lines.insert(at, f"{name}: {value}")
            close += 1
    result: str = "\n".join(lines)
    parse_issue(result, config, path)
    return result


def update_issue(path: Path, config: Config, changes: dict[str, str | None]) -> None:
    """Apply `changes` to an issue file's header in place (see edit_header)."""
    write_file(path, edit_header(read_text(path), config, path, changes), config.newline)


def write_edits(edits: list[tuple[Path, str]], config: Config) -> None:
    """Write texts that edit_header already produced (and so validated), one per file.

    For changes that span files: compute every file's new text first, so a bad
    one is refused before any is written, then write them all.
    """
    for path, text in edits:
        write_file(path, text, config.newline)


def read_text(path: Path) -> str:
    """An issue file's text. Invalid UTF-8 is a PunchError naming the line."""
    data: bytes = path.read_bytes()
    try:
        text: str = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PunchError("not valid UTF-8", path, data.count(b"\n", 0, exc.start) + 1) from None
    # The same newline translation read_text() does in text mode.
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_issue(path: Path, config: Config) -> Issue:
    """Read and parse one issue file."""
    return parse_issue(read_text(path), config, path)


def issue_files(config: Config) -> list[Path]:
    """Every `.md` file in the issues directory or one of its buckets, placed right or not.

    Files directly in the issues directory are included so a misplaced one is
    an error rather than invisible. Deeper files (comments) are not issues.
    """
    if not config.issues_path.is_dir():
        return []
    found: list[Path] = [*config.issues_path.glob("*.md"), *config.issues_path.glob("*/*.md")]
    return sorted(path for path in found if path.is_file())


def placement_error(path: Path, config: Config) -> PunchError | None:
    """Why the issue file at `path` isn't where its filename says it belongs, or None."""
    try:
        expected: Path = config.issue_path(path.stem)
    except PunchError as exc:
        return PunchError(exc.message, path)
    if path == expected:
        return None
    return PunchError(
        f"in the wrong bucket; it belongs at {expected.relative_to(config.root).as_posix()}", path
    )


def load_issues(config: Config) -> list[Issue]:
    """Every issue, in bucket and filename order. One bad or misplaced file fails the lot."""
    issues: list[Issue] = []
    for path in issue_files(config):
        error: PunchError | None = placement_error(path, config)
        if error is not None:
            raise error
        issues.append(read_issue(path, config))
    return issues


def write_issue(issue: Issue, config: Config, *, create: bool = False) -> Path:
    """Write an issue to `config.issue_path(id)`, in the config's line endings.

    The text is parsed back before writing, so this never writes a file that
    `punch check` would reject. With `create`, an existing file is an error
    rather than overwritten.
    """
    path: Path = config.issue_path(issue.id)
    text: str = format_issue(issue, config)
    parse_issue(text, config, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        write_file(path, text, config.newline, create=create)
    except FileExistsError:
        raise PunchError("already exists", path) from None
    issue.path = path
    return path
