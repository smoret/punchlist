"""Issue id generation."""

from __future__ import annotations

import re
from collections.abc import Iterator

import pytest
from helpers import write_raw

from punchlist.config import Config
from punchlist.errors import PunchError
from punchlist.ids import ALPHABET, new_id, resolve_id


def test_alphabet_is_crockford_lowercase() -> None:
    assert len(ALPHABET) == 32
    assert len(set(ALPHABET)) == 32
    assert not set("ilou") & set(ALPHABET)


def test_shape(config: Config) -> None:
    assert re.fullmatch(rf"pl-[{ALPHABET}]{{5}}", new_id(config))


def _scripted(chars: str) -> Iterator[str]:
    yield from chars


def test_redraws_on_existing_file(config: Config) -> None:
    write_raw(config, "pl-aaaaa", "")
    script = _scripted("aaaaa" + "bbbbb")
    assert new_id(config, lambda _alphabet: next(script)) == "pl-bbbbb"


def test_redraws_on_taken(config: Config) -> None:
    script = _scripted("aaaaa" + "bbbbb")
    assert new_id(config, lambda _alphabet: next(script), taken={"pl-aaaaa"}) == "pl-bbbbb"


def test_gives_up_when_space_is_full(config: Config) -> None:
    write_raw(config, "pl-aaaaa", "")
    with pytest.raises(PunchError, match="raise id_length"):
        new_id(config, lambda _alphabet: "a")


@pytest.fixture
def three(config: Config) -> Config:
    """Issue files pl-7k3qx, pl-7k9ab, and pl-7k3 (an id that is a prefix of another)."""
    for stem in ("pl-7k3qx", "pl-7k9ab", "pl-7k3"):
        write_raw(config, stem, "")
    return config


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("pl-7k3qx", "pl-7k3qx"),
        ("pl-7k3q", "pl-7k3qx"),
        ("7k3q", "pl-7k3qx"),
        ("7k9", "pl-7k9ab"),
        ("pl-7k3", "pl-7k3"),  # an exact match wins over being a prefix
    ],
)
def test_resolve(three: Config, text: str, expected: str) -> None:
    assert resolve_id(three, text) == expected


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("7k", "'7k' is ambiguous: pl-7k3, pl-7k3qx, pl-7k9ab"),
        ("zz", "no issue matches 'zz'"),
        ("", "empty issue id"),
    ],
)
def test_resolve_rejects(three: Config, text: str, message: str) -> None:
    with pytest.raises(PunchError, match=re.escape(message)):
        resolve_id(three, text)


def test_resolve_without_issues_dir(config: Config) -> None:
    with pytest.raises(PunchError, match="no issue matches"):
        resolve_id(config, "pl-7k3qx")


def test_resolve_searches_every_bucket(three: Config) -> None:
    write_raw(three, "pl-a1234", "")
    assert resolve_id(three, "a1") == "pl-a1234"
    with pytest.raises(PunchError, match=re.escape("'pl-' is ambiguous: pl-7k3, pl-7k3qx")):
        resolve_id(three, "pl-")


def test_resolve_misplaced_only_is_an_error(three: Config) -> None:
    (three.issues_path / "z").mkdir()
    (three.issues_path / "z" / "pl-a1234.md").write_text("", encoding="utf-8")
    with pytest.raises(PunchError, match="wrong bucket; it belongs at issues/a/pl-a1234.md"):
        resolve_id(three, "a12")


def test_resolve_prefers_the_placed_copy(three: Config) -> None:
    (three.issues_path / "pl-7k3qx.md").write_text("", encoding="utf-8")  # stray flat copy
    assert resolve_id(three, "7k3q") == "pl-7k3qx"
