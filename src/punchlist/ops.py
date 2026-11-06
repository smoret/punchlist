"""Operations every front end shares: validated changes to issues, and the queries that
order and judge them. `cli` and `web` call these and only render the results, so a rule
(a dependency loop, a related link's other side, an enum value) lives in one place.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from pathlib import Path

from .check import dependency_loop, parent_loop
from .config import DEPENDS_ON, RELATED, REQUIRED_FIELDS, Config
from .errors import PunchError
from .git import git_user
from .ids import new_id, resolve_id
from .issue import (
    FieldValue, Issue, edit_body, edit_header, parse_issue, rank_error, read_issue, read_text,
    write_edits, write_issue,
)

# A `set_fields` assignment: (field, operator, value), the operator "" for `=`, "+" or "-".
Assignment = tuple[str, str, str]


class Conflict(PunchError):
    """A change made against a value the file no longer has: someone else changed it first."""


@dataclass(frozen=True)
class SetResult:
    """What `set_fields` changed: each field, with any list items it dropped, and the
    related partners it also wrote."""

    issue_id: str
    changed: list[tuple[str, list[str]]]
    partners: list[str]


def sort_key(config: Config, issue: Issue) -> tuple[int, bool, float, str, str]:
    """Priority in config order, then rank, then created, then id; missing values sort last."""
    priority: str | None = issue.text("priority")
    level: int = config.priorities.index(priority) if priority else len(config.priorities)
    rank: float | None = issue.rank
    return (level, rank is None, rank or 0.0, issue.text("created") or "", issue.id)


def is_blocked(issue: Issue, statuses: dict[str, str]) -> bool:
    """Whether any dependency isn't closed; a missing one counts, since it can't be done."""
    return any(statuses.get(dependency) != "closed" for dependency in issue.items(DEPENDS_ON))


def resolve_author(flag: str | None, env: str | None, root: Path) -> str:
    """--author, else $PUNCH_AUTHOR (`env`, which the caller reads), else git's user.name;
    an error if none is set."""
    author: str = (flag or env or git_user(root) or "").strip()
    if not author:
        raise PunchError("no author: pass --author, set PUNCH_AUTHOR, or set git's user.name")
    if "\n" in author or "\r" in author:
        raise PunchError("author must be a single line")
    return author


def json_fields(issue: Issue, config: Config) -> dict[str, FieldValue]:
    """An issue's header fields in config order, for JSON output."""
    return {name: issue.fields[name] for name in config.fields if name in issue.fields}


def field_text(issue: Issue, name: str) -> str | None:
    """A field as its header text (list fields joined), or None if absent."""
    value: FieldValue | None = issue.fields.get(name)
    return ", ".join(value) if isinstance(value, list) else value


def link_ids(config: Config, field: str, values: list[str]) -> list[str]:
    """A link field's comma-separated ids (short ids allowed) -> full ids, in order, no repeats."""
    if field not in config.fields:
        raise PunchError(f"{field} is not a field in the config")
    texts: list[str] = [item.strip() for value in values for item in value.split(",")]
    if not all(texts):
        raise PunchError(f"empty item in {field}")
    return list(dict.fromkeys(resolve_id(config, text) for text in texts))


def create_issue(config: Config, fields: dict[str, str], *, labels: list[str] | None = None,
                 depends_on: list[str] | None = None, related: list[str] | None = None,
                 body: str = "") -> tuple[Issue, list[str]]:
    """Write a new issue and return it with the related partners linked back to it.

    `fields` holds scalar header fields; `title` and `type` are required, `status`
    defaults to open, and `parent` may be a short id. Everything is checked before
    any file is written, so an error names the value rather than a file.
    """
    values: dict[str, str] = {"status": "open", **fields}
    values["title"] = values.get("title", "").strip()
    if not values["title"]:
        raise PunchError("title must not be empty")
    if "type" not in values:
        raise PunchError("type is required")
    if "parent" in values:
        values["parent"] = resolve_id(config, values["parent"])
    for name, value in values.items():
        if name not in config.fields:
            raise PunchError(f"{name} is not a field in the config")
        if name in config.list_fields or name in ("id", "created"):
            raise PunchError(f"{name} can't be given as a plain field")
        if "\n" in value or "\r" in value:
            raise PunchError(f"{name} must be a single line")
        error: str | None = config.choice_error(name, value)
        if error is None and name == "rank":
            error = rank_error(value)
        if error is not None:
            raise PunchError(error)
    values["created"] = datetime.date.today().isoformat()
    values["id"] = new_id(config)
    issue_fields: dict[str, FieldValue] = dict(values)
    if labels:
        if "labels" not in config.list_fields:
            raise PunchError("labels is not a list field in the config")
        items: list[str] = [item.strip() for label in labels for item in label.split(",")]
        if not all(items):
            raise PunchError("empty label")
        issue_fields["labels"] = items
    if depends_on:
        # A new issue can't close a loop: nothing depends on it yet.
        issue_fields[DEPENDS_ON] = link_ids(config, DEPENDS_ON, depends_on)
    partners: list[tuple[str, Path, str]] = []
    if related:
        linked: list[str] = link_ids(config, RELATED, related)
        issue_fields[RELATED] = linked
        partners = _partner_edits(config, values["id"], [], linked)
    issue = Issue(issue_fields, body)
    write_issue(issue, config, create=True)
    write_edits([(path, text) for _other, path, text in partners], config)
    return issue, [other for other, _path, _text in partners]


def set_fields(config: Config, issue_id: str, assignments: list[Assignment],
               expect: dict[str, str | None] | None = None) -> SetResult:
    """Set, add to, or remove header fields of one issue, as `punch set` does.

    `expect` maps a field to the text it must still have (None: absent); a field
    that has changed since the caller read it raises `Conflict` and writes nothing.
    A related change also edits each partner, and every file's new text is computed
    and validated before any is written.
    """
    path: Path = config.issue_path(issue_id)
    issue: Issue = read_issue(path, config)
    for name, wanted in (expect or {}).items():
        if field_text(issue, name) != wanted:
            raise Conflict(f"{issue_id} {name} is now {field_text(issue, name) or 'unset'}, "
                           f"not {wanted or 'unset'}")
    changes: dict[str, str | None] = {}
    for name, op, raw in assignments:
        if name in changes:
            raise PunchError(f"{name} is set twice")
        if name not in config.fields:
            raise PunchError(f"{name} is not a field in the config")
        if name == "id":
            raise PunchError("id cannot be changed; it names the file")
        value: str = raw.strip()
        if op:
            value = _list_after(config, issue, name, op, value)
        changes[name] = _set_value(config, issue_id, name, value)

    changed: list[str] = [
        name for name, value in changes.items() if field_text(issue, name) != value
    ]
    if not changed:
        return SetResult(issue_id, [], [])
    text: str = edit_header(read_text(path), config, path, {n: changes[n] for n in changed})
    partners: list[tuple[str, Path, str]] = []
    if RELATED in changed:
        new: str | None = changes[RELATED]
        partners = _partner_edits(config, issue_id, issue.items(RELATED),
                                  new.split(", ") if new else [])
    write_edits([(path, text), *((p, t) for _o, p, t in partners)], config)
    report: list[tuple[str, list[str]]] = []
    for name in changed:
        removed: list[str] = []
        if name in config.list_fields:
            kept: str | None = changes[name]
            removed = [i for i in issue.items(name) if i not in (kept.split(", ") if kept else [])]
        report.append((name, removed))
    return SetResult(issue_id, report, [other for other, _path, _text in partners])


def set_body(config: Config, issue_id: str, body: str, expect: str | None = None) -> bool:
    """Replace an issue's body, keeping its header byte for byte; return whether it changed.

    Blank lines around `body` are trimmed and line endings normalized, as `new
    --body` does. With `expect` (the body the caller read), a body changed since
    raises `Conflict` and writes nothing.
    """
    path: Path = config.issue_path(issue_id)
    text: str = read_text(path)
    issue: Issue = parse_issue(text, config, path)
    if expect is not None and issue.body != expect:
        raise Conflict(f"{issue_id} body has changed since it was read")
    trimmed: str = body.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    new: str = trimmed + "\n" if trimmed.strip() else ""
    if new == issue.body:
        return False
    write_edits([(path, edit_body(text, config, path, new))], config)
    return True


def _partner_edits(config: Config, issue_id: str, old: list[str],
                   new: list[str]) -> list[tuple[str, Path, str]]:
    """The other side of a related-link change: (partner id, its path, its new text).

    Each newly related issue gains `issue_id`; each one no longer related loses
    it. Partners already in the right state are left out. Computed (and
    validated) before anything is written.
    """
    edits: list[tuple[str, Path, str]] = []
    for other in [o for o in new if o not in old] + [o for o in old if o not in new]:
        path: Path = config.issue_path(other)
        if not path.is_file():
            continue  # a dangling id being removed; check reports dangling ones
        items: list[str] = read_issue(path, config).items(RELATED)
        if other in new and issue_id not in items:
            value: str | None = ", ".join([*items, issue_id])
        elif other not in new and issue_id in items:
            value = ", ".join(i for i in items if i != issue_id) or None
        else:
            continue
        edits.append((other, path, edit_header(read_text(path), config, path, {RELATED: value})))
    return edits


def _list_after(config: Config, issue: Issue, name: str, op: str, value: str) -> str:
    """The whole list `field+=value` or `field-=value` leaves, as `=` would take it.

    Adding an id already there or removing one that isn't changes nothing, so a
    retry is safe. `-=` matches a stored id exactly before resolving it, so a
    link to a deleted issue can still be removed.
    """
    if name not in config.list_fields:
        raise PunchError(f"{name}{op}= only works on list fields")
    current: list[str] = issue.items(name)
    texts: list[str] = [item.strip() for item in value.split(",")]
    if not all(texts):
        raise PunchError(f"empty item in {name}{op}=")
    links: bool = name in (DEPENDS_ON, RELATED)
    if op == "+":
        added: list[str] = link_ids(config, name, texts) if links else texts
        return ", ".join(dict.fromkeys([*current, *added]))
    gone: set[str] = {
        text if text in current or not links else resolve_id(config, text) for text in texts
    }
    return ", ".join(item for item in current if item not in gone)


def _set_value(config: Config, issue_id: str, name: str, value: str) -> str | None:
    """The header text `set_fields` writes for one assignment; None removes the field."""
    if not value:
        if name in REQUIRED_FIELDS:
            raise PunchError(f"{name} is required and cannot be removed")
        return None
    if name == DEPENDS_ON:
        dependencies: list[str] = link_ids(config, DEPENDS_ON, [value])
        cycle: list[str] | None = dependency_loop(config, issue_id, dependencies)
        if cycle is not None:
            raise PunchError(f"dependency loop: {' -> '.join(cycle)}")
        return ", ".join(dependencies)
    if name == RELATED:
        related: list[str] = link_ids(config, RELATED, [value])
        if issue_id in related:
            raise PunchError(f"{issue_id} can't be related to itself")
        return ", ".join(related)
    if name in config.list_fields:
        items: list[str] = [item.strip() for item in value.split(",")]
        if not all(items):
            raise PunchError(f"empty item in {name}")
        return ", ".join(items)
    error: str | None = config.choice_error(name, value)
    if error is None and name == "rank":
        error = rank_error(value)
    if error is not None:
        raise PunchError(error)
    if name == "parent":
        value = resolve_id(config, value)
        loop: list[str] | None = parent_loop(config, issue_id, value)
        if loop is not None:
            raise PunchError(f"parent loop: {' -> '.join(loop)}")
    return value
