"""The issue file format: parsing, failing closed, and byte-for-byte round trips."""

from __future__ import annotations

from pathlib import Path

import pytest

from punchlist.config import Config
from punchlist.errors import PunchError
from punchlist.issue import (
    Issue, edit_body, edit_header, format_issue, parse_issue, read_issue, write_issue,
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

PATH = Path("issues/7/pl-7k3qx.md")


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
    assert path == config.issues_path / "7" / "pl-7k3qx.md"
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
        (_header(*BASE, "rank: 1e3"), 7, "rank '1e3' is not a number like 2 or 2.5"),
        (_header(*BASE, "rank: .5"), 7, "rank '.5' is not a number"),
        (_header(*BASE, "rank: nan"), 7, "rank 'nan' is not a number"),
        (_header(*BASE, "rank: +2"), 7, r"rank '\+2' is not a number"),
        (_header(*BASE, "rank: 2."), 7, r"rank '2\.' is not a number"),
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


def test_edit_header_changes_one_line(config: Config) -> None:
    assert edit_header(SAMPLE, config, PATH, {"status": "closed"}) == SAMPLE.replace(
        "status: open", "status: closed"
    )


def test_edit_header_keeps_hand_edited_order(config: Config) -> None:
    text = "---\ncreated: 2026-10-18\nstatus: open\nid: pl-aaaaa\ntitle: T\ntype: bug\n---\nbody\n"
    assert edit_header(text, config, PATH, {"status": "closed"}) == text.replace("open", "closed")


def test_edit_header_ignores_body_lookalike(config: Config) -> None:
    text = _header(*BASE) + "\nstatus: open in the body\n"
    result = edit_header(text, config, PATH, {"status": "closed"})
    assert result.endswith("\nstatus: open in the body\n")
    assert "status: closed\n" in result


def test_edit_header_inserts_in_config_order(config: Config) -> None:
    # Config order: id title type status priority category parent labels created.
    result = edit_header(_header(*BASE), config, PATH,
                         {"labels": "a, b", "priority": "high", "parent": "pl-bbbbb"})
    assert result == _header("id: pl-aaaaa", "title: T", "type: bug", "status: open",
                             "priority: high", "parent: pl-bbbbb", "labels: a, b",
                             "created: 2026-10-18")


def test_edit_header_inserts_into_hand_edited_order(config: Config) -> None:
    # After the last line whose field comes before it in config order.
    text = _header("created: 2026-10-18", "status: open", "id: pl-aaaaa", "title: T", "type: bug")
    assert edit_header(text, config, PATH, {"priority": "low"}) == _header(
        "created: 2026-10-18", "status: open", "id: pl-aaaaa", "title: T", "type: bug",
        "priority: low",
    )


def test_edit_header_removes(config: Config) -> None:
    text = _header(*BASE[:4], "priority: low", "labels: a", *BASE[4:]) + "\nbody\n"
    assert edit_header(text, config, PATH, {"priority": None, "labels": None, "parent": None}) == (
        _header(*BASE) + "\nbody\n"
    )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"status": "done"}, "status 'done' is not one of"),
        ({"status": None}, "missing required field.*status"),
        ({"created": "soon"}, "not a YYYY-MM-DD date"),
        ({"title": "x\nstatus: closed"}, "title must be a single line"),
        ({"title": "x\rstatus: closed"}, "title must be a single line"),
    ],
)
def test_edit_header_rejects(config: Config, changes: dict[str, str | None], message: str) -> None:
    with pytest.raises(PunchError, match=message):
        edit_header(_header(*BASE), config, PATH, changes)


def test_edit_body_keeps_the_header_bytes(config: Config) -> None:
    # A hand-edited header (fields out of config order) survives untouched.
    head = "---\ncreated: 2026-10-18\nstatus: open\nid: pl-aaaaa\ntitle: T\ntype: bug\n---\n"
    assert edit_body(head + "\nold\n", config, PATH, "new\n\n---\nlooks like a header\n") == (
        head + "\nnew\n\n---\nlooks like a header\n")
    assert edit_body(head + "\nold\n", config, PATH, "") == head
    assert edit_body(head, config, PATH, "first\n") == head + "\nfirst\n"
    assert edit_body(SAMPLE, config, PATH, "Reasons.\n") == (
        SAMPLE[:SAMPLE.index("\nFree-form")] + "\nReasons.\n")


@pytest.mark.parametrize(("text", "body", "message"), [
    (_header(*BASE), "no newline", "body must end with a newline"),
    (_header(*BASE, "priority: urgent"), "x\n", "priority 'urgent'"),
    ("id: pl-aaaaa\n", "x\n", "first line must be '---'"),
])
def test_edit_body_rejects(config: Config, text: str, body: str, message: str) -> None:
    with pytest.raises(PunchError, match=message):
        edit_body(text, config, PATH, body)


def test_value_kept_verbatim(config: Config) -> None:
    issue = parse_issue(_header(*BASE[:1], "title:  'x' -- \"y\" #z ", *BASE[2:]), config, PATH)
    assert issue.title == " 'x' -- \"y\" #z "


@pytest.mark.parametrize(("text", "number"), [("2", 2.0), ("-3", -3.0), ("3.75", 3.75), ("007", 7.0)])
def test_rank(config: Config, text: str, number: float) -> None:
    issue = parse_issue(_header(*BASE, f"rank: {text}"), config, PATH)
    assert issue.text("rank") == text  # kept as written
    assert issue.rank == number


def test_unranked(config: Config) -> None:
    assert parse_issue(_header(*BASE), config, PATH).rank is None
