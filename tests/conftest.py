"""Shared fixtures. Every issue root is built under tmp_path, never in the repo."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from punchlist.config import CONFIG_NAME, Config, default_config, load_config


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A project root with a default config (prefix `pl`) and two categories."""
    data = default_config("pl")
    data["categories"] = ["tooling", "docs"]
    (tmp_path / CONFIG_NAME).write_text(json.dumps(data), encoding="utf-8")
    return tmp_path


@pytest.fixture
def config(root: Path) -> Config:
    """The loaded config of `root`."""
    return load_config(root)
