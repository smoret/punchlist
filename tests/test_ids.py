"""Issue id generation."""

from __future__ import annotations

import re
from collections.abc import Iterator

import pytest

from punchlist.config import Config
from punchlist.errors import PunchError
from punchlist.ids import ALPHABET, new_id


def test_alphabet_is_crockford_lowercase() -> None:
    assert len(ALPHABET) == 32
    assert len(set(ALPHABET)) == 32
    assert not set("ilou") & set(ALPHABET)


def test_shape(config: Config) -> None:
    assert re.fullmatch(rf"pl-[{ALPHABET}]{{5}}", new_id(config))


def _scripted(chars: str) -> Iterator[str]:
    yield from chars


def test_redraws_on_existing_file(config: Config) -> None:
    config.issues_path.mkdir()
    (config.issues_path / "pl-aaaaa.md").write_text("", encoding="utf-8")
    script = _scripted("aaaaa" + "bbbbb")
    assert new_id(config, lambda _alphabet: next(script)) == "pl-bbbbb"


def test_gives_up_when_space_is_full(config: Config) -> None:
    config.issues_path.mkdir()
    (config.issues_path / "pl-aaaaa.md").write_text("", encoding="utf-8")
    with pytest.raises(PunchError, match="raise id_length"):
        new_id(config, lambda _alphabet: "a")
