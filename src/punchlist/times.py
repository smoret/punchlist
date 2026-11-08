"""When each issue was created and last changed, to the second, from git and the files.

Nothing is stored: an issue's header has only a `created` date. The times come
from one `git log` over the issues directory -- the first commit that touched an
issue's file or its comments created it, the last one changed it -- and the
files themselves. A file with uncommitted changes, or one git doesn't track,
counts from its modification time, so an edit shows up before it is committed.
Modification times alone can't stand in for history: a clone or checkout resets
them. Outside a repository with full history (no git, no commits yet, a shallow
clone), every time is a modification time and `Times.exact` is False.
"""

from __future__ import annotations

import datetime
import os
from dataclasses import dataclass
from pathlib import Path

from .comment import timestamp
from .config import Config
from .git import file_times, has_history, uncommitted
from .history import issue_of


@dataclass(frozen=True)
class IssueTimes:
    """Seconds since the epoch: when the issue appeared, and its latest change, its
    comments included."""

    created: float
    updated: float


@dataclass(frozen=True)
class Times:
    """Every issue's times, by id; `exact` is False when they are modification times only."""

    by_id: dict[str, IssueTimes]
    exact: bool


def utc_text(seconds: float) -> str:
    """A time as `2026-10-19T14:12:05Z`, the form comments use."""
    return timestamp(datetime.datetime.fromtimestamp(seconds, datetime.timezone.utc))


def issue_times(config: Config) -> Times:
    """The created and updated time of every issue with a file under the issues directory."""
    root: Path = config.root
    pathspec: str = config.issues_path.relative_to(root).as_posix()
    exact: bool = has_history(root)
    committed: dict[str, tuple[int, int]] = file_times(root, [pathspec]) if exact else {}
    dirty: set[str] = uncommitted(root, [pathspec]) if exact else set()

    created: dict[str, float] = {}
    updated: dict[str, float] = {}

    def note(issue_id: str, first: float, last: float) -> None:
        created[issue_id] = min(created.get(issue_id, first), first)
        updated[issue_id] = max(updated.get(issue_id, last), last)

    for path, (first, last) in committed.items():
        issue_id: str | None = issue_of(path)
        if issue_id is not None:
            note(issue_id, first, last)
    for folder, _dirs, files in os.walk(config.issues_path):
        for name in files:
            full: Path = Path(folder, name)
            path = full.relative_to(root).as_posix()
            found: str | None = issue_of(path)
            if found is None or (path in committed and path not in dirty):
                continue
            try:
                mtime: float = full.stat().st_mtime
            except OSError:
                continue  # removed while walking
            # A new file is as old as it looks; an edited one changed now but was
            # created when history says.
            note(found, committed[path][0] if path in committed else mtime, mtime)
    return Times({i: IssueTimes(first, updated[i]) for i, first in created.items()}, exact)
