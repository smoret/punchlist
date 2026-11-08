"""Every git subprocess punchlist runs: the author default, and reading an issue's history.

History costs a fixed number of git calls however many commits there are:
`git log` lists commits (with `--name-status` when the changed files matter),
and one `git cat-file --batch` fetches every file version the classifier
needs. Paths are posix and relative to the punchlist root, which may be a
subdirectory of the repository (`git log --relative`, `<rev>:./<path>`).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .errors import PunchError

# Starts each commit in `git log` output; a commit message can't contain it in practice.
RECORD = "\x1e"
LOG_FORMAT = f"--format={RECORD}%H%x00%ct%x00%ad%x00%an%x00%s%x00%B%x00"


@dataclass(frozen=True)
class Change:
    """One file a commit touched: status `A`, `M`, `D`, or `R` (moved from `old_path`)."""

    status: str
    path: str
    old_path: str


@dataclass(frozen=True)
class Commit:
    """One commit as `git log` reported it."""

    sha: str
    time: int  # committer time, seconds since the epoch; orders commits from separate logs
    date: str  # author date, YYYY-MM-DD
    author: str
    subject: str
    message: str
    changes: tuple[Change, ...] = ()


def git_user(root: Path) -> str | None:
    """git config user.name as seen from `root`, or None if git or the setting is missing."""
    try:
        result = subprocess.run(["git", "config", "user.name"], cwd=root, capture_output=True,
                                text=True, encoding="utf-8", check=False)
    except OSError:
        return None
    return result.stdout.strip() or None if result.returncode == 0 else None


def _run(root: Path, args: list[str], stdin: bytes = b"") -> bytes:
    """git's stdout for `args`, run in `root`; a PunchError with git's last stderr line if not."""
    try:
        result = subprocess.run(["git", *args], cwd=root, input=stdin, capture_output=True,
                                check=False)
    except OSError as exc:
        raise PunchError(f"cannot run git: {exc.strerror}") from None
    if result.returncode != 0:
        lines: list[str] = result.stderr.decode("utf-8", "replace").strip().splitlines()
        raise PunchError(f"git {args[0]} failed: {lines[-1] if lines else result.returncode}")
    return result.stdout


def require_history(root: Path) -> None:
    """Refuse unless `root` is in a git work tree with its full history."""
    try:
        _run(root, ["rev-parse", "--is-inside-work-tree"])
    except PunchError:
        raise PunchError("not in a git repository; history is read from git") from None
    if _run(root, ["rev-parse", "--is-shallow-repository"]).strip() == b"true":
        raise PunchError("shallow clone, so history is incomplete; "
                         "run `git fetch --unshallow` to get all of it")


def has_history(root: Path) -> bool:
    """Whether `root` is in a git work tree with its full history and at least one commit."""
    try:
        require_history(root)
        _run(root, ["rev-parse", "--verify", "--quiet", "HEAD"])
    except PunchError:
        return False
    return True


def file_times(root: Path, pathspecs: list[str]) -> dict[str, tuple[int, int]]:
    """Every path a commit touched under `pathspecs`, deleted ones too: the author times
    (seconds since the epoch) of the first and last commits that did. A move counts as
    a delete of one path and an add of the other."""
    text: str = _run(root, ["log", f"--format={RECORD}%at", "-z", "--name-only", "--no-renames",
                            "--relative", "--", *pathspecs]).decode("utf-8", "replace")
    times: dict[str, tuple[int, int]] = {}
    for record in text.split(RECORD)[1:]:
        tokens: list[str] = record.split("\0")
        time: int = int(tokens[0])
        for token in tokens[1:]:
            path: str = token.strip("\n")
            if path:
                first, last = times.get(path, (time, time))
                times[path] = (min(first, time), max(last, time))
    return times


def uncommitted(root: Path, pathspecs: list[str]) -> set[str]:
    """Paths under `pathspecs` that differ from HEAD, staged or not, or that git doesn't
    track and doesn't ignore."""
    changed: bytes = _run(root, ["diff", "HEAD", "--name-only", "-z", "--no-renames",
                                 "--relative", "--", *pathspecs])
    untracked: bytes = _run(root, ["ls-files", "-z", "--others", "--exclude-standard", "--",
                                   *pathspecs])
    return {p for p in (changed + untracked).decode("utf-8", "replace").split("\0") if p}


def resolve_rev(root: Path, rev: str) -> str:
    """The full sha of the commit `rev` names; an error if it names none."""
    if rev.startswith("-"):  # would be read as an option
        raise PunchError(f"unknown revision {rev!r}")
    try:
        sha: bytes = _run(root, ["rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"])
    except PunchError:
        raise PunchError(f"unknown revision {rev!r}") from None
    return sha.decode().strip()


def log(root: Path, args: list[str], pathspecs: list[str] | None = None) -> list[Commit]:
    """Commits `git log <args>` lists, oldest first.

    With `pathspecs`, only commits touching them, each with the matching files
    it changed (renames detected); without, no changed files are read.
    """
    command: list[str] = ["log", "--reverse", "--date=short", LOG_FORMAT, *args]
    if pathspecs is not None:
        command += ["-z", "-M", "--name-status", "--relative", "--", *pathspecs]
    text: str = _run(root, command).decode("utf-8", "replace")
    commits: list[Commit] = []
    for record in text.split(RECORD)[1:]:
        sha, time, date, author, subject, message, rest = record.split("\0", 6)
        commits.append(Commit(sha, int(time), date, author, subject, message.strip("\n"),
                              _changes(rest) if pathspecs is not None else ()))
    return commits


def _changes(text: str) -> tuple[Change, ...]:
    """`--name-status -z` output after a commit's header: `M\\0path\\0`, `R100\\0old\\0new\\0`."""
    tokens: list[str] = text.lstrip("\0\n").split("\0")
    changes: list[Change] = []
    i: int = 0
    while i < len(tokens) and tokens[i]:
        status: str = tokens[i][0]
        if status in "RC":
            changes.append(Change(status, tokens[i + 2], tokens[i + 1]))
            i += 3
        else:
            changes.append(Change(status, tokens[i + 1], tokens[i + 1]))
            i += 2
    return tuple(changes)


def read_blobs(root: Path, specs: list[str]) -> list[bytes | None]:
    """The content of each `<rev>:./<path>` in `specs`, in order; None where none exists."""
    if not specs:
        return []
    data: bytes = _run(root, ["cat-file", "--batch"], "".join(f"{s}\n" for s in specs).encode())
    blobs: list[bytes | None] = []
    at: int = 0
    for _spec in specs:
        end: int = data.index(b"\n", at)
        header: list[bytes] = data[at:end].split(b" ")
        at = end + 1
        if header[-1] == b"missing" or len(header) != 3:
            blobs.append(None)
            continue
        size: int = int(header[2])
        blobs.append(data[at:at + size] if header[1] == b"blob" else None)
        at += size + 1  # the content, then a newline
    return blobs
