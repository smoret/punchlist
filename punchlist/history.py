"""Issue history: what a commit did to an issue, named from its files before and after.

`classify` turns one changed file -- the issue's own file or one of its
comments -- into events: `created`, `closed`, `reopened`, a `field` change
(old -> new; a list field's whole list), `body` (+N -M lines), `moved`,
`deleted`, `reformatted` (changed, but no field or body line did), and
`comment-added`, `comment-edited`, `comment-deleted`. A commit that only
mentions the issue's id is `referenced`. Every history view reads its events
from here, so their names and JSON agree.

Old revisions are read leniently: they record the past, not what is valid
now, so a field or enum value today's config no longer has is still a change
worth naming. Only the shared header syntax is required; a revision without
it is `malformed`, and the next readable one is `repaired`. This module runs
no git and prints nothing.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .errors import PunchError
from .git import Change, Commit
from .issue import FieldValue, split_header

CLOSED = "closed"


@dataclass(frozen=True)
class Event:
    """One thing a commit did to an issue. Which attributes are set depends on `kind`."""

    kind: str
    field: str | None = None  # `field`, `closed`, `reopened`: the field, then its values
    old: FieldValue | None = None
    new: FieldValue | None = None
    header: dict[str, FieldValue] | None = None  # `created`: the first header
    added: int | None = None  # `body`: lines added and removed
    removed: int | None = None
    path: str | None = None  # `moved`: the new path
    author: str | None = None  # comment events

    def describe(self) -> str:
        """The event as `punch history` prints it: `priority low -> high`, `body +6 -2`."""
        if self.kind == "created":
            header: dict[str, FieldValue] = self.header or {}
            parts: list[str] = [str(header.get("type", "?"))]
            if "parent" in header:
                parts.append(f"parent {header['parent']}")
            return f"created ({', '.join(parts)})"
        if self.kind == "field":
            if isinstance(self.old, list) or isinstance(self.new, list):
                old: list[str] = self.old if isinstance(self.old, list) else []
                new: list[str] = self.new if isinstance(self.new, list) else []
                return " ".join([str(self.field), *(f"+{i}" for i in new if i not in old),
                                 *(f"-{i}" for i in old if i not in new)])
            if self.field == "title":  # titles are long; the new one says enough
                return f"title -> {self.new}"
            return f"{self.field} {self.old or 'none'} -> {self.new or 'none'}"
        if self.kind == "body":
            return f"body +{self.added} -{self.removed}"
        if self.kind == "moved":
            return f"moved to {self.path}"
        if self.kind.startswith("comment-"):
            return f"{self.kind.replace('-', ' ')} ({self.author})"
        return self.kind

    def as_json(self) -> dict[str, object]:
        """The event as a JSON record: `kind`, then whichever attributes it has."""
        row: dict[str, object] = {"kind": self.kind}
        if self.field is not None:
            row.update(field=self.field, old=self.old, new=self.new)
        for name in ("header", "added", "removed", "path", "author"):
            value: object = getattr(self, name)
            if value is not None:
                row[name] = value
        return row


@dataclass(frozen=True)
class Entry:
    """One commit's events for one issue."""

    issue_id: str
    commit: Commit
    events: tuple[Event, ...]

    def as_json(self) -> dict[str, object]:
        """The entry as a JSON record: the issue, the commit's details, then its events."""
        return {
            "id": self.issue_id, "sha": self.commit.sha, "date": self.commit.date,
            "author": self.commit.author, "subject": self.commit.subject,
            "events": [event.as_json() for event in self.events],
        }


@dataclass(frozen=True)
class Revision:
    """An issue file's header fields and body as of one commit, read leniently."""

    fields: dict[str, FieldValue]
    body: str


def read_revision(data: bytes, config: Config, path: str) -> Revision | None:
    """A past version of an issue file, or None if it lacks the header syntax.

    Any `key: value` line counts, known to the config or not; list fields are
    split as the config lists them now.
    """
    fields: dict[str, str] | None = _header(data, path)
    if fields is None:
        return None
    text: str = _decode(data) or ""
    body: str = split_header(text, Path(path)).body
    return Revision({
        name: [item.strip() for item in value.split(",")] if name in config.list_fields
        else value
        for name, value in fields.items()
    }, body)


def _decode(data: bytes) -> str | None:
    try:
        return data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError:
        return None


def _header(data: bytes, path: str) -> dict[str, str] | None:
    """A file's header as raw `key: value` pairs, or None if it lacks the header syntax."""
    text: str | None = _decode(data)
    if text is None:
        return None
    try:
        lines: list[tuple[int, str]] = split_header(text, Path(path)).lines
    except PunchError:
        return None
    fields: dict[str, str] = {}
    for _lineno, line in lines:
        name, sep, value = line.partition(": ")
        if not sep or name in fields:
            return None
        fields[name] = value
    return fields


def owns(path: str, issue_id: str) -> bool:
    """Whether `path` is `issue_id`'s file or one of its comments, in whatever bucket."""
    parts: list[str] = path.split("/")
    return parts[-1] == f"{issue_id}.md" or (
        len(parts) >= 3 and parts[-3] == issue_id and parts[-2] == "comments")


def is_comment(path: str) -> bool:
    """Whether `path`, one that `owns` matched, is a comment rather than the issue file."""
    return "/comments/" in f"/{path}"


def classify(change: Change, old: bytes | None, new: bytes | None,
             config: Config) -> list[Event]:
    """The events one changed file amounts to. `old` is the file before the commit
    (at `change.old_path`), `new` after (at `change.path`); None where it didn't exist."""
    if is_comment(change.path):
        return _comment_events(change, old, new)
    if new is None:
        return [Event("deleted")]
    after: Revision | None = read_revision(new, config, change.path)
    if old is None:
        return [Event("created", header=after.fields)] if after else [Event("malformed")]
    events: list[Event] = []
    if change.old_path != change.path:
        events.append(Event("moved", path=change.path))
    before: Revision | None = read_revision(old, config, change.old_path)
    if after is None:
        return [*events, Event("malformed")]
    if before is None:
        return [*events, Event("repaired")]
    if old == new:
        return events
    changed: list[Event] = diff(before, after, config)
    return [*events, *(changed or [Event("reformatted")])]


def diff(before: Revision, after: Revision, config: Config) -> list[Event]:
    """What changed between two revisions: status first, then fields in config order
    (fields the config doesn't know after, by name), then the body."""
    events: list[Event] = []
    old_status: FieldValue | None = before.fields.get("status")
    new_status: FieldValue | None = after.fields.get("status")
    if old_status != new_status:
        kind: str = ("closed" if new_status == CLOSED
                     else "reopened" if old_status == CLOSED else "field")
        events.append(Event(kind, "status", old_status, new_status))
    names: list[str] = [*config.fields, *sorted(
        (set(before.fields) | set(after.fields)) - set(config.fields))]
    for name in names:
        old: FieldValue | None = before.fields.get(name)
        new: FieldValue | None = after.fields.get(name)
        if name != "status" and old != new:
            events.append(Event("field", name, old, new))
    if before.body != after.body:
        added: int = 0
        removed: int = 0
        matcher = difflib.SequenceMatcher(
            None, before.body.splitlines(), after.body.splitlines(), autojunk=False)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag != "equal":
                removed += i2 - i1
                added += j2 - j1
        events.append(Event("body", added=added, removed=removed))
    return events


def _comment_events(change: Change, old: bytes | None, new: bytes | None) -> list[Event]:
    if old is None:
        return [Event("comment-added", author=_author(new, change.path))]
    if new is None:
        return [Event("comment-deleted", author=_author(old, change.old_path))]
    if old == new:
        return []  # moved along with its issue; the issue file says so
    return [Event("comment-edited", author=_author(new, change.path))]


def _author(data: bytes | None, path: str) -> str:
    """A comment file's `author`, or `?` if it has none readable."""
    fields: dict[str, str] | None = None if data is None else _header(data, path)
    return (fields or {}).get("author", "?")


def mentions(commit: Commit, issue_id: str, config: Config) -> bool:
    """Whether `commit`'s message mentions `issue_id`, by the config's `mention_pattern`."""
    return config.mention_regex(issue_id).search(commit.message) is not None
