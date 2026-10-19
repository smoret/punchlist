"""Plain helpers shared by tests (fixtures live in conftest.py)."""

from __future__ import annotations

from pathlib import Path

from punchlist.config import Config


def write_raw(config: Config, issue_id: str, content: str | bytes) -> Path:
    """Write a file, valid or not, where `issue_id` belongs; return its path."""
    path: Path = config.issue_path(issue_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path
