"""Comments: one file per comment, beside the issue in its bucket.

    issues/7/pl-7k3qx/comments/20261019T141205Z-claude.md

    ---
    author: claude
    created: 2026-10-19T14:12:05Z
    ---

    Tried X; blocked on Y.

The format is the issue header format with its own fixed schema: `author`
and `created` (a UTC timestamp to the second), both required, nothing else.
The filename is the timestamp, compact, then a slug of the author, so
filenames sort by time and two people commenting at once never touch the
same file; the same author twice in one second gets `-2`, `-3`, ...
Comment files are created exclusively and never rewritten.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .errors import PunchError
from .issue import DELIMITER, Header, read_text, split_header

COMMENT_FIELDS: tuple[str, ...] = ("author", "created")
CREATED_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")

# Same-second comments by one author before giving up; only a runaway loop gets here.
MAX_SEQUENCE = 100


@dataclass(frozen=True)
class Comment:
    """One comment, plus where it was read from."""

    author: str
    created: str  # 2026-10-19T14:12:05Z
    body: str
    path: Path | None = None
    sequence: int = 1  # 1 for `<stamp>-<slug>.md`, n for `-n.md`


def author_slug(author: str) -> str:
    """The filename form of an author: `Steve Moret` -> `steve-moret`."""
    slug: str = re.sub(r"[^a-z0-9]+", "-", author.lower()).strip("-")
    if not slug:
        raise PunchError(f"author {author!r} has no letters or digits for a filename")
    return slug


def timestamp(when: datetime.datetime) -> str:
    """`when` in UTC as a `created` value: `2026-10-19T14:12:05Z`."""
    return when.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def file_stem(created: str, author: str) -> str:
    """The filename, minus any `-n` and `.md`: `20261019T141205Z-claude`."""
    return f"{created.replace('-', '').replace(':', '')}-{author_slug(author)}"


def format_comment(comment: Comment) -> str:
    """Serialize a comment. Inverse of parse_comment."""
    for name, value in (("author", comment.author), ("created", comment.created)):
        if "\n" in value or "\r" in value:
            raise PunchError(f"{name} must be a single line")
    return (f"{DELIMITER}\nauthor: {comment.author}\ncreated: {comment.created}\n{DELIMITER}\n"
            f"\n{comment.body}")


def parse_comment(text: str, path: Path) -> Comment:
    """Parse a comment file. Its name must match its header: `<stamp>-<slug>[-n].md`."""
    header: Header = split_header(text, path)
    values: dict[str, str] = {}
    for lineno, line in header.lines:
        name, sep, value = line.partition(": ")
        if not sep:
            raise PunchError(f"expected 'field: value', got {line!r}", path, lineno)
        if name not in COMMENT_FIELDS:
            raise PunchError(f"unknown comment field {name!r}", path, lineno)
        if not value.strip():
            raise PunchError(f"empty value for {name!r}", path, lineno)
        if name in values:
            raise PunchError(f"duplicate field {name!r}", path, lineno)
        if name == "created" and not _valid_created(value):
            raise PunchError(f"created {value!r} is not a UTC time like 2026-10-19T14:12:05Z",
                             path, lineno)
        values[name] = value
    missing: list[str] = [f for f in COMMENT_FIELDS if f not in values]
    if missing:
        raise PunchError(f"missing required field(s): {', '.join(missing)}", path,
                         header.close_line)
    if not header.body.strip():
        raise PunchError("comment has no body", path, header.body_line)

    stem: str = file_stem(values["created"], values["author"])
    sequence: int = _sequence(path.stem, stem)
    if sequence == 0:
        raise PunchError(f"filename does not match the header; expected {stem}.md", path)
    return Comment(values["author"], values["created"], header.body, path, sequence)


def _valid_created(value: str) -> bool:
    if not CREATED_RE.fullmatch(value):
        return False
    try:
        datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return True


def _sequence(actual: str, expected: str) -> int:
    """1 if `actual` is `expected`, n if it is `expected-n` (n >= 2), else 0."""
    if actual == expected:
        return 1
    suffix: str = actual.removeprefix(f"{expected}-")
    if suffix != actual and suffix.isdigit() and not suffix.startswith("0") and int(suffix) >= 2:
        return int(suffix)
    return 0


def read_comment(path: Path) -> Comment:
    """Read and parse one comment file."""
    return parse_comment(read_text(path), path)


def load_comments(config: Config, issue_id: str) -> list[Comment]:
    """An issue's comments, oldest first. One bad file fails the lot."""
    directory: Path = config.comments_path(issue_id)
    if not directory.is_dir():
        return []
    comments: list[Comment] = []
    for path in directory.iterdir():
        if not path.is_file() or path.suffix != ".md":
            raise PunchError("not a comment file (expected <time>-<author>.md)", path)
        comments.append(read_comment(path))
    return sorted(comments, key=lambda c: (c.created, c.sequence, str(c.path)))


def write_comment(config: Config, issue_id: str, author: str, body: str,
                  when: datetime.datetime) -> Path:
    """Write a new comment file for `issue_id`; never overwrites. Returns its path."""
    created: str = timestamp(when)
    stem: str = file_stem(created, author)
    directory: Path = config.comments_path(issue_id)
    for sequence in range(1, MAX_SEQUENCE + 1):
        path: Path = directory / (f"{stem}.md" if sequence == 1 else f"{stem}-{sequence}.md")
        text: str = format_comment(Comment(author, created, body))
        parse_comment(text, path)  # never write a file check would reject
        directory.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("x", encoding="utf-8") as file:
                file.write(text)
        except FileExistsError:
            continue
        return path
    raise PunchError(f"{MAX_SEQUENCE} comments by {author!r} in one second; refusing more")
