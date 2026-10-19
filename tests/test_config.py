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
        ({"fields": ["id", "title", "type", "status"]}, "required field.*created"),
        ({"fields": ["id", "title", "type", "status", "created", "Bad"]}, "field name"),
        ({"list_fields": ["nope"]}, "not in fields"),
        ({"list_fields": ["status"]}, "cannot be a list field"),
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


def test_rejects_missing_key() -> None:
    data: dict[str, Any] = default_config("pl")
    del data["categories"]
    with pytest.raises(PunchError, match="missing key.*categories"):
        parse_config(data, Path("."), Path(CONFIG_NAME))


def test_default_config_is_json_round_trippable() -> None:
    data = default_config("pl")
    assert json.loads(json.dumps(data)) == data
    _parse()
