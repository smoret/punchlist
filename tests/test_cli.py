"""The `punch` command line, driven through main()."""

from __future__ import annotations

from pathlib import Path

import pytest

from punchlist.cli import main
from punchlist.config import CONFIG_NAME, load_config


def test_init_writes_loadable_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                     capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["init", "--prefix", "pl"]) == 0
    assert load_config(tmp_path).prefix == "pl"
    assert capsys.readouterr().out.startswith("wrote ")


def test_init_refuses_existing(root: Path, monkeypatch: pytest.MonkeyPatch,
                               capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.chdir(root)
    before: str = (root / CONFIG_NAME).read_text(encoding="utf-8")
    assert main(["init", "--prefix", "pl"]) == 1
    assert "already exists" in capsys.readouterr().err
    assert (root / CONFIG_NAME).read_text(encoding="utf-8") == before


def test_init_rejects_bad_prefix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                 capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["init", "--prefix", "P-L"]) == 1
    assert "lowercase" in capsys.readouterr().err
    assert not (tmp_path / CONFIG_NAME).exists()
