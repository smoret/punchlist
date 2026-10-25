"""Plain helpers shared by tests (fixtures live in conftest.py)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from punchlist.config import Config


def git(root: Path, *args: str, date: str = "2026-03-02T12:00:00") -> str:
    """Run git in `root` as a fixed test identity, dating any commit `date`; return stdout."""
    env: dict[str, str] = {
        **os.environ, "GIT_AUTHOR_NAME": "Tester", "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "Tester", "GIT_COMMITTER_EMAIL": "t@example.com",
        "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date,
    }
    return subprocess.run(["git", "-c", "commit.gpgsign=false", *args], cwd=root, env=env,
                          check=True, capture_output=True, text=True, encoding="utf-8").stdout


def commit(root: Path, message: str, date: str = "2026-03-02T12:00:00") -> str:
    """Stage everything under `root` and commit it; return the new commit's sha."""
    git(root, "add", "-A", ".")
    git(root, "commit", "-q", "--allow-empty", "-m", message, date=date)
    return git(root, "rev-parse", "HEAD").strip()


def write_raw(config: Config, issue_id: str, content: str | bytes) -> Path:
    """Write a file, valid or not, where `issue_id` belongs; return its path."""
    path: Path = config.issue_path(issue_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path
