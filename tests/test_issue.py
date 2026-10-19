"""The issue file format: parsing, failing closed, and byte-for-byte round trips."""

from __future__ import annotations

from pathlib import Path

import pytest

from punchlist.config import Config
from punchlist.errors import PunchError
from punchlist.issue import (
    Issue, format_issue, parse_issue, read_issue, replace_field, write_issue,
)

SAMPLE = """\
---
id: pl-7k3qx
title: "Retry" should back off on timeouts: quoted, colons
type: task
status: open
priority: low
category: docs
parent: pl-2x9fa
labels: network, deferred
created: 2026-10-09
---

Free-form markdown body.

## Why deferred

Reasons.
"""

PATH = Path("issues/pl-7k3qx.md")


def test_parse_sample(config: Config) -> None:
    issue = parse_issue(SAMPLE, config, PATH)
    assert issue.id == "pl-7k3qx"
    assert issue.title == '"Retry" should back off on timeouts: quoted, colons'
    assert issue.status == "open"
    assert issue.text("parent") == "pl-2x9fa"
    assert issue.items("labels") == ["network", "deferred"]
    assert issue.body.startswith("Free-form markdown body.\n")
    assert issue.body.endswith("Reasons.\n")


def test_format_round_trips(config: Config) -> None:
    assert format_issue(parse_issue(SAMPLE, config, PATH), config) == SAMPLE


def test_fields_written_in_config_order(config: Config) -> None:
    issue = Issue({"created": "2026-10-18", "status": "open", "type": "bug", "title": "T", "id": "pl-aaaaa"})
    assert format_issue(issue, config) == (
        "---\nid: pl-aaaaa\ntitle: T\ntype: bug\nstatus: open\ncreated: 2026-10-18\n---\n"
    )


def test_empty_body_round_trips(config: Config) -> None:
    text = "---\nid: pl-aaaaa\ntitle: T\ntype: bug\nstatus: open\ncreated: 2026-10-18\n---\n"
    issue = parse_issue(text, config, PATH)
    assert issue.body == ""
    assert format_issue(issue, config) == text


def test_write_round_trips_bytes(config: Config) -> None:
    issue = parse_issue(SAMPLE, config, PATH)
    path = write_issue(issue, config)
    assert path == config.issues_path / "pl-7k3qx.md"
    first: bytes = path.read_bytes()
    write_issue(read_issue(path, config), config)
    assert path.read_bytes() == first


def test_write_refuses_invalid_issue(config: Config) -> None:
    issue = Issue({"id": "pl-aaaaa", "title": "T", "type": "nope", "status": "open", "created": "2026-10-18"})
    with pytest.raises(PunchError, match="type 'nope'"):
        write_issue(issue, config)
    assert not config.issues_path.exists()


def test_format_refuses_multiline_value(config: Config) -> None:
    issue = Issue({"id": "pl-aaaaa", "title": "a\nb", "type": "bug", "status": "open", "created": "2026-10-18"})
    with pytest.raises(PunchError, match="single line"):
        format_issue(issue, config)


def _header(*lines: str) -> str:
    return "\n".join(["---", *lines, "---", ""])


BASE: tuple[str, ...] = ("id: pl-aaaaa", "title: T", "type: bug", "status: open", "created: 2026-10-18")


@pytest.mark.parametrize(
    ("text", "line", "message"),
    [
        ("id: pl-aaaaa\n", 1, "first line must be '---'"),
        ("---\nid: pl-aaaaa\n", 1, "no closing"),
        (_header(*BASE, "nonsense"), 7, "expected 'field: value'"),
        (_header(*BASE, "parent:"), 7, "expected 'field: value'"),
        (_header(*BASE, "parent:  "), 7, "empty value"),
        (_header(*BASE, "colour: red"), 7, "unknown field 'colour'"),
        (_header(*BASE, "id: pl-bbbbb"), 7, "duplicate field 'id'"),
        (_header(*BASE, "priority: urgent"), 7, "priority 'urgent' is not one of: high, medium, low"),
        (_header(*BASE, "category: web"), 7, "category 'web'"),
        (_header(*BASE, "labels: a,,b"), 7, "empty item"),
        (_header(*BASE[:4], "created: 2026-13-01"), 6, "not a YYYY-MM-DD date"),
        (_header(*BASE[:4], "created: 2026-1-01"), 6, "not a YYYY-MM-DD date"),
        (_header(*BASE[:4]), 6, "missing required field.*created"),
    ],
)
def test_fails_closed_with_line(config: Config, text: str, line: int, message: str) -> None:
    with pytest.raises(PunchError, match=message) as info:
        parse_issue(text, config, PATH)
    assert info.value.line == line
    assert str(info.value).startswith(f"{PATH.as_posix()}:{line}: ")


def test_no_categories_configured_rejects_any(config: Config) -> None:
    bare = Config(**{**config.__dict__, "categories": ()})
    with pytest.raises(PunchError, match="none configured"):
        parse_issue(_header(*BASE, "category: tooling"), bare, PATH)


def test_replace_field_changes_one_line(config: Config) -> None:
    assert replace_field(SAMPLE, config, PATH, "status", "closed") == SAMPLE.replace(
        "status: open", "status: closed"
    )


def test_replace_field_keeps_hand_edited_order(config: Config) -> None:
    text = "---\ncreated: 2026-10-18\nstatus: open\nid: pl-aaaaa\ntitle: T\ntype: bug\n---\nbody\n"
    assert replace_field(text, config, PATH, "status", "closed") == text.replace("open", "closed")


def test_replace_field_ignores_body_lookalike(config: Config) -> None:
    text = _header(*BASE) + "\nstatus: open in the body\n"
    result = replace_field(text, config, PATH, "status", "closed")
    assert result.endswith("\nstatus: open in the body\n")
    assert "status: closed\n" in result


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("status", "done", "status 'done' is not one of"),
        ("priority", "high", "no 'priority' field"),
    ],
)
def test_replace_field_rejects(config: Config, name: str, value: str, message: str) -> None:
    with pytest.raises(PunchError, match=message):
        replace_field(_header(*BASE), config, PATH, name, value)


def test_value_kept_verbatim(config: Config) -> None:
    issue = parse_issue(_header(*BASE[:1], "title:  'x' -- \"y\" #z ", *BASE[2:]), config, PATH)
    assert issue.title == " 'x' -- \"y\" #z "
