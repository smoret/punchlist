"""Root discovery and punchlist.json validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from punchlist.config import CONFIG_NAME, Config, default_config, find_root, load_config, parse_config
from punchlist.errors import PunchError


def test_find_root_walks_up(root: Path) -> None:
    nested = root / "a" / "b"
    nested.mkdir(parents=True)
    assert find_root(nested) == root.resolve()


def test_find_root_fails_without_config(tmp_path: Path) -> None:
    with pytest.raises(PunchError, match="run `punch init`"):
        find_root(tmp_path)


def test_load_default(config: Config, root: Path) -> None:
    assert config.prefix == "pl"
    assert config.id_length == 5
    assert config.fanout == 1
    assert config.issues_path == root / "issues"
    assert config.priorities == ("high", "medium", "low")
    assert config.allowed("category") == ("tooling", "docs")
    assert config.allowed("title") is None


def test_invalid_json_names_line(tmp_path: Path) -> None:
    (tmp_path / CONFIG_NAME).write_text('{\n  "prefix": \n}', encoding="utf-8")
    with pytest.raises(PunchError, match=r"punchlist\.json:3: invalid JSON"):
        load_config(tmp_path)


def _parse(**changes: Any) -> Config:
    data: dict[str, Any] = default_config("pl")
    data.update(changes)
    return parse_config(data, Path("."), Path(CONFIG_NAME))


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"extra": 1}, "unknown key"),
        ({"prefix": "PL"}, "lowercase"),
        ({"prefix": ""}, "non-empty string"),
        ({"id_length": 0}, "positive integer"),
        ({"id_length": True}, "positive integer"),
        ({"fanout": 0}, r"fanout must be an integer from 1 to id_length \(5\)"),
        ({"fanout": 6}, "fanout must be"),
        ({"fanout": True}, "fanout must be"),
        ({"fanout": "1"}, "fanout must be"),
        ({"fields": ["id", "title", "type", "status"]}, "required field.*created"),
        ({"fields": ["id", "title", "type", "status", "created", "Bad"]}, "field name"),
        ({"list_fields": ["nope"]}, "not in fields"),
        ({"list_fields": ["status"]}, "cannot be a list field"),
        ({"list_fields": ["rank"]}, "'rank' cannot be a list field"),
        ({"types": []}, "types must not be empty"),
        ({"statuses": ["open", "done"]}, "'closed'"),
        ({"categories": ["a", "a"]}, "duplicate"),
        ({"categories": ["a,b"]}, "commas"),
        ({"categories": [" a"]}, "surrounding spaces"),
        ({"priorities": "high"}, "list of non-empty strings"),
    ],
)
def test_rejects(changes: dict[str, Any], message: str) -> None:
    with pytest.raises(PunchError, match=message):
        _parse(**changes)


@pytest.mark.parametrize(
    ("fanout", "issue_id", "expected"),
    [
        (1, "pl-7k3qx", "issues/7/pl-7k3qx.md"),
        (2, "pl-7k3qx", "issues/7k/pl-7k3qx.md"),
        (5, "pl-7k3qx", "issues/7k3qx/pl-7k3qx.md"),
        (1, "old-ab", "issues/a/old-ab.md"),  # an earlier prefix still buckets on its random part
    ],
)
def test_issue_path(fanout: int, issue_id: str, expected: str) -> None:
    assert _parse(fanout=fanout).issue_path(issue_id).as_posix() == expected


@pytest.mark.parametrize("issue_id", ["notes", "pl-", "pl-a"])
def test_issue_path_rejects(issue_id: str) -> None:
    with pytest.raises(PunchError, match="needs a '-' and at least 2 characters after it"):
        _parse(fanout=2).issue_path(issue_id)


def test_rejects_missing_key() -> None:
    data: dict[str, Any] = default_config("pl")
    del data["categories"]
    with pytest.raises(PunchError, match="missing key.*categories"):
        parse_config(data, Path("."), Path(CONFIG_NAME))


def test_optional_key_may_be_absent() -> None:
    # A punchlist.json written before check_references existed still loads.
    data: dict[str, Any] = default_config("pl")
    del data["check_references"]
    assert parse_config(data, Path("."), Path(CONFIG_NAME)).check_references is False


def test_init_writes_optional_keys() -> None:
    assert default_config("pl")["check_references"] is False
    assert _parse(check_references=True).check_references is True


@pytest.mark.parametrize("value", ["yes", 1, None])
def test_optional_key_type_is_checked(value: Any) -> None:
    with pytest.raises(PunchError, match="check_references must be true or false"):
        _parse(check_references=value)


def test_mention_pattern() -> None:
    default: Config = _parse()
    assert default.mention_regex("pl-7k3qx").search("pl-7k3qx: slice")
    assert not default.mention_regex("pl-7k3qx").search("pl-7k3qxy")
    # {short} is the id without its prefix; regex braces of the pattern's own survive.
    short: Config = _parse(mention_pattern=r"fixes {short}\d{0,2}\b")
    assert short.mention_regex("pl-7k3qx").search("fixes 7k3qx12")


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("", "mention_pattern must be a non-empty string"),
        (1, "mention_pattern must be a non-empty string"),
        (r"\bpl-\w+", r"mention_pattern must contain \{id\} or \{short\}"),
        ("({id}", "mention_pattern is not a valid regex"),
    ],
)
def test_mention_pattern_is_checked(value: Any, message: str) -> None:
    with pytest.raises(PunchError, match=message):
        _parse(mention_pattern=value)


def test_line_endings() -> None:
    # Absent means the platform's, as before the key existed.
    assert _parse().newline is None
    assert _parse(line_endings="lf").newline == "\n"
    assert _parse(line_endings="crlf").newline == "\r\n"


@pytest.mark.parametrize("value", ["LF", "cr", "", None, ["lf"]])
def test_line_endings_are_checked(value: Any) -> None:
    with pytest.raises(PunchError, match="line_endings must be one of: platform, lf, crlf"):
        _parse(line_endings=value)


def test_misspelled_optional_key_is_unknown() -> None:
    with pytest.raises(PunchError, match="unknown key.*check_refrences"):
        _parse(check_refrences=True)


def test_default_config_is_json_round_trippable() -> None:
    data = default_config("pl")
    assert json.loads(json.dumps(data)) == data
    _parse()
