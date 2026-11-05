"""punchlist.json: finding the project root, and loading and validating its config.

The config's presence marks the root, found by walking up from the working
directory the way git finds `.git`. Everything enumerable (types, statuses,
priorities, categories, the field list) lives here, so changing them needs no
code change. Validation fails closed: an unknown key or a wrongly typed value
is an error, not a default. Keys added after trackers already existed are
optional (`OPTIONAL_KEYS`), so an older punchlist.json keeps loading; every
other key is required.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import PunchError

CONFIG_NAME = "punchlist.json"

REQUIRED_FIELDS: tuple[str, ...] = ("id", "title", "type", "status", "created")

# The list field naming the issues an issue waits on. Only the waiting side
# stores the link; "what does X block" is derived by reading every issue.
DEPENDS_ON = "depends-on"

# The list field naming issues related without either waiting on the other.
# Unlike depends-on it is stored on both issues, so `show` needs no scan;
# `set` and `new` keep the two sides in step and `check` reports a one-sided link.
RELATED = "related"

# Header field -> the config key listing its allowed values.
ENUM_FIELDS: dict[str, str] = {
    "type": "types",
    "status": "statuses",
    "priority": "priorities",
    "category": "categories",
}

# Keys an older punchlist.json may lack, and the value each then takes. A new
# key goes here rather than being required, or every existing tracker would
# stop loading the moment punchlist updates.
OPTIONAL_KEYS: dict[str, Any] = {
    "check_references": False,
    "mention_pattern": r"\b{id}\b",
    "line_endings": "platform",
}

# What `mention_pattern` may name: the full id, or the id without its prefix.
MENTION_PLACEHOLDERS: tuple[str, ...] = ("{id}", "{short}")

# `line_endings` -> the `newline` argument of open(): None translates to the
# platform's endings (CRLF on Windows, matching a `core.autocrlf` checkout).
LINE_ENDINGS: dict[str, str | None] = {"platform": None, "lf": "\n", "crlf": "\r\n"}

PREFIX_RE = re.compile(r"[a-z0-9]+")
FIELD_RE = re.compile(r"[a-z][a-z0-9_-]*")


@dataclass(frozen=True)
class Config:
    """A validated punchlist.json, plus the root directory it was found in."""

    root: Path
    prefix: str
    id_length: int
    fanout: int
    issues_dir: str
    fields: tuple[str, ...]
    list_fields: tuple[str, ...]
    types: tuple[str, ...]
    statuses: tuple[str, ...]
    priorities: tuple[str, ...]  # highest first
    categories: tuple[str, ...]
    check_references: bool = False  # `check` also flags body mentions of missing ids
    mention_pattern: str = OPTIONAL_KEYS["mention_pattern"]  # commits that mention an issue
    line_endings: str = OPTIONAL_KEYS["line_endings"]  # what every write uses; see write_file

    @property
    def newline(self) -> str | None:
        """`line_endings` as open()'s `newline` argument."""
        return LINE_ENDINGS[self.line_endings]

    @property
    def issues_path(self) -> Path:
        """The directory holding the bucket directories."""
        return self.root / self.issues_dir

    def issue_path(self, issue_id: str) -> Path:
        """Where an issue lives: `<issues_dir>/<bucket>/<id>.md`. The only id-to-path mapping.

        The bucket is the first `fanout` characters of the id's random part (the
        prefix is constant, so it can't spread files). Buckets keep each folder
        under the 1,000 entries GitHub's web UI lists. An issue's comments go
        beside its file: `issue_path(id).with_suffix("") / "comments"`.
        """
        _prefix, sep, random = issue_id.partition("-")
        if not sep or len(random) < self.fanout:
            raise PunchError(
                f"id {issue_id!r} needs a '-' and at least {self.fanout} characters after it"
            )
        return self.issues_path / random[:self.fanout] / f"{issue_id}.md"

    @property
    def has_links(self) -> bool:
        """Whether this tracker has the `depends-on` field."""
        return DEPENDS_ON in self.fields

    @property
    def has_related(self) -> bool:
        """Whether this tracker has the `related` field."""
        return RELATED in self.fields

    def mention_regex(self, issue_id: str) -> re.Pattern[str]:
        """`mention_pattern` for one issue: `{id}` and `{short}` replaced, matched literally."""
        return _mention_regex(self.mention_pattern, issue_id)

    def comments_path(self, issue_id: str) -> Path:
        """The directory holding an issue's comments, beside its file in its bucket."""
        return self.issue_path(issue_id).with_suffix("") / "comments"

    def allowed(self, field: str) -> tuple[str, ...] | None:
        """The allowed values of an enumerated field, or None if `field` is free text."""
        key: str | None = ENUM_FIELDS.get(field)
        return None if key is None else getattr(self, key)

    def choice_error(self, field: str, value: str) -> str | None:
        """Why `value` isn't allowed for `field`, or None if it is (or `field` is free text)."""
        allowed: tuple[str, ...] | None = self.allowed(field)
        if allowed is None or value in allowed:
            return None
        choices: str = ", ".join(allowed) if allowed else "none configured"
        return f"{field} {value!r} is not one of: {choices}"


def default_config(prefix: str) -> dict[str, Any]:
    """The punchlist.json that `punch init` writes."""
    return {
        "prefix": prefix,
        "id_length": 5,
        "fanout": 1,
        "issues_dir": "issues",
        "fields": [
            "id", "title", "type", "status", "priority", "rank", "category", "parent",
            "depends-on", "related", "labels", "created",
        ],
        "list_fields": ["depends-on", "related", "labels"],
        "types": ["epic", "feature", "task", "bug", "subtask"],
        "statuses": ["open", "closed"],
        "priorities": ["high", "medium", "low"],
        "categories": [],
        **OPTIONAL_KEYS,
    }


def find_root(start: Path) -> Path:
    """The nearest directory at or above `start` containing punchlist.json."""
    start = start.resolve()
    for directory in (start, *start.parents):
        if (directory / CONFIG_NAME).is_file():
            return directory
    raise PunchError(
        f"no {CONFIG_NAME} in {start} or any parent; run `punch init` at the project root"
    )


def load_config(root: Path) -> Config:
    """Read and validate `root`/punchlist.json."""
    path: Path = root / CONFIG_NAME
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PunchError(f"invalid JSON: {exc.msg}", path, exc.lineno) from exc
    return parse_config(data, root, path)


def init_config(root: Path, prefix: str, line_endings: str = "platform") -> Path:
    """Write a default punchlist.json in `root`, itself in `line_endings`; refuse if one exists."""
    path: Path = root / CONFIG_NAME
    if path.exists():
        raise PunchError("already exists", path)
    if not PREFIX_RE.fullmatch(prefix):
        raise PunchError(f"prefix {prefix!r} must be lowercase letters and digits")
    data: dict[str, Any] = {**default_config(prefix), "line_endings": line_endings}
    config: Config = parse_config(data, root, path)
    write_file(path, json.dumps(data, indent=2) + "\n", config.newline)
    return path


def add_categories(root: Path, names: list[str]) -> Config:
    """Append `names` to punchlist.json's categories, rewrite it, and return the new config."""
    path: Path = root / CONFIG_NAME
    load_config(root)  # validate before touching it
    data: Any = json.loads(path.read_text(encoding="utf-8"))
    data["categories"] = [*data["categories"], *names]
    config: Config = parse_config(data, root, path)
    write_file(path, json.dumps(data, indent=2) + "\n", config.newline)
    return config


def write_file(path: Path, text: str, newline: str | None, *, create: bool = False) -> None:
    """Write `text` (with `\\n` line endings) as UTF-8, translating them to `newline`.

    Every file punch writes goes through here, so `line_endings` applies to all
    of them. With `create`, an existing file raises FileExistsError.
    """
    with path.open("x" if create else "w", encoding="utf-8", newline=newline) as file:
        file.write(text)


def parse_config(data: Any, root: Path, path: Path) -> Config:
    """Validate decoded punchlist.json contents. `path` is only for error messages."""
    if not isinstance(data, dict):
        raise PunchError("top level must be a JSON object", path)
    expected: set[str] = set(default_config("x"))
    unknown: list[str] = sorted(set(data) - expected)
    if unknown:
        raise PunchError(f"unknown key(s): {', '.join(unknown)}", path)
    missing: list[str] = sorted(expected - set(data) - set(OPTIONAL_KEYS))
    if missing:
        raise PunchError(f"missing key(s): {', '.join(missing)}", path)
    data = {**OPTIONAL_KEYS, **data}
    check_references: Any = data["check_references"]
    if not isinstance(check_references, bool):
        raise PunchError("check_references must be true or false", path)
    mention_pattern: str = _string(data, "mention_pattern", path)
    if not any(p in mention_pattern for p in MENTION_PLACEHOLDERS):
        raise PunchError(f"mention_pattern must contain {' or '.join(MENTION_PLACEHOLDERS)}",
                         path)
    try:
        _mention_regex(mention_pattern, "x-0")
    except re.error as exc:
        raise PunchError(f"mention_pattern is not a valid regex: {exc}", path) from None
    line_endings: Any = data["line_endings"]
    if not isinstance(line_endings, str) or line_endings not in LINE_ENDINGS:
        raise PunchError(f"line_endings must be one of: {', '.join(LINE_ENDINGS)}", path)

    prefix: str = _string(data, "prefix", path)
    if not PREFIX_RE.fullmatch(prefix):
        raise PunchError(f"prefix {prefix!r} must be lowercase letters and digits", path)
    id_length: Any = data["id_length"]
    if not isinstance(id_length, int) or isinstance(id_length, bool) or id_length < 1:
        raise PunchError("id_length must be a positive integer", path)
    fanout: Any = data["fanout"]
    if not isinstance(fanout, int) or isinstance(fanout, bool) or not 1 <= fanout <= id_length:
        raise PunchError(f"fanout must be an integer from 1 to id_length ({id_length})", path)
    issues_dir: str = _string(data, "issues_dir", path)

    fields: tuple[str, ...] = _string_list(data, "fields", path)
    for field in fields:
        if not FIELD_RE.fullmatch(field):
            raise PunchError(f"field name {field!r} must match {FIELD_RE.pattern}", path)
    absent: list[str] = [f for f in REQUIRED_FIELDS if f not in fields]
    if absent:
        raise PunchError(f"fields must include required field(s): {', '.join(absent)}", path)
    list_fields: tuple[str, ...] = _string_list(data, "list_fields", path)
    for field in list_fields:
        if field not in fields:
            raise PunchError(f"list field {field!r} is not in fields", path)
        if field in REQUIRED_FIELDS or field in ENUM_FIELDS or field in ("rank", "parent"):
            raise PunchError(f"{field!r} cannot be a list field", path)
    for link in (DEPENDS_ON, RELATED):
        if link in fields and link not in list_fields:
            raise PunchError(f"{link!r} must also be in list_fields", path)

    enums: dict[str, tuple[str, ...]] = {}
    for key in ENUM_FIELDS.values():
        enums[key] = _string_list(data, key, path)
        if not enums[key] and key != "categories":
            raise PunchError(f"{key} must not be empty", path)
    if "open" not in enums["statuses"] or "closed" not in enums["statuses"]:
        raise PunchError("statuses must include 'open' and 'closed'", path)

    return Config(
        root=root,
        prefix=prefix,
        id_length=id_length,
        fanout=fanout,
        issues_dir=issues_dir,
        fields=fields,
        list_fields=list_fields,
        types=enums["types"],
        statuses=enums["statuses"],
        priorities=enums["priorities"],
        categories=enums["categories"],
        check_references=check_references,
        mention_pattern=mention_pattern,
        line_endings=line_endings,
    )


def _mention_regex(pattern: str, issue_id: str) -> re.Pattern[str]:
    # Plain replace, not str.format: a regex has braces of its own (`\d{2}`).
    short: str = issue_id.partition("-")[2]
    return re.compile(pattern.replace("{id}", re.escape(issue_id))
                      .replace("{short}", re.escape(short)))


def _string(data: dict[str, Any], key: str, path: Path) -> str:
    value: Any = data[key]
    if not isinstance(value, str) or not value:
        raise PunchError(f"{key} must be a non-empty string", path)
    return value


def _string_list(data: dict[str, Any], key: str, path: Path) -> tuple[str, ...]:
    value: Any = data[key]
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise PunchError(f"{key} must be a list of non-empty strings", path)
    items: tuple[str, ...] = tuple(value)
    if len(set(items)) != len(items):
        raise PunchError(f"{key} has duplicate entries", path)
    for item in items:
        if item != item.strip() or "," in item:
            raise PunchError(
                f"{key} entry {item!r} must not contain commas or surrounding spaces", path
            )
    return items
