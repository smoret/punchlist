"""git: the author default, and reading logs and file versions."""

from __future__ import annotations

from pathlib import Path

import pytest
from helpers import commit, git

from punchlist.errors import PunchError
from punchlist.git import Change, git_user, log, read_blobs, require_history, resolve_rev


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """An empty git repository."""
    git(tmp_path, "init", "-q")
    return tmp_path


def test_git_user_reads_the_repo(repo: Path) -> None:
    git(repo, "config", "user.name", "Repo Person")
    assert git_user(repo) == "Repo Person"
    assert git_user(repo / "nowhere") is None  # not a directory: git can't run there


def test_log_lists_changes_oldest_first(repo: Path) -> None:
    (repo / "a").mkdir()
    (repo / "a" / "x.md").write_text("one\n", encoding="utf-8")
    first: str = commit(repo, "Add x\n\nWith a body.", "2026-03-01T10:00:00")
    git(repo, "mv", "a", "b")
    (repo / "b" / "y.md").write_text("two\n", encoding="utf-8")
    second: str = commit(repo, "Move x, add y", "2026-03-02T10:00:00")

    commits = log(repo, [], ["*.md"])
    assert [c.sha for c in commits] == [first, second]
    assert (commits[0].date, commits[0].author, commits[0].subject, commits[0].message) == (
        "2026-03-01", "Tester", "Add x", "Add x\n\nWith a body.")
    assert commits[0].changes == (Change("A", "a/x.md", "a/x.md"),)
    assert set(commits[1].changes) == {Change("R", "b/x.md", "a/x.md"),
                                       Change("A", "b/y.md", "b/y.md")}
    # Without pathspecs, no changed files are read.
    assert [c.changes for c in log(repo, [f"{first}..HEAD"])] == [()]


def test_log_paths_are_relative_to_a_subdirectory(repo: Path) -> None:
    sub: Path = repo / "sub"
    (sub / "issues").mkdir(parents=True)
    (sub / "issues" / "x.md").write_text("one\n", encoding="utf-8")
    sha: str = commit(repo, "Add")
    [entry] = log(sub, [], ["issues/*.md"])
    assert entry.changes == (Change("A", "issues/x.md", "issues/x.md"),)
    assert read_blobs(sub, [f"{sha}:./issues/x.md", f"{sha}^:./issues/x.md",
                            f"{sha}:./issues"]) == [b"one\n", None, None]


def test_read_blobs_keeps_bytes_and_order(repo: Path) -> None:
    (repo / "x").write_bytes(b"a\nb\n\n")
    (repo / "y").write_bytes(b"")
    sha: str = commit(repo, "Add")
    assert read_blobs(repo, [f"{sha}:./y", f"{sha}:./nope", f"{sha}:./x"]) == [
        b"", None, b"a\nb\n\n"]
    assert not read_blobs(repo, [])


def test_require_history(repo: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    commit(repo, "First")
    commit(repo, "Second")
    require_history(repo)
    with pytest.raises(PunchError, match="not in a git repository"):
        require_history(tmp_path_factory.mktemp("plain"))
    shallow: Path = tmp_path_factory.mktemp("clones") / "shallow"
    git(repo, "clone", "-q", "--depth", "1", repo.as_uri(), str(shallow))
    with pytest.raises(PunchError, match="git fetch --unshallow"):
        require_history(shallow)


def test_resolve_rev(repo: Path) -> None:
    sha: str = commit(repo, "First")
    assert resolve_rev(repo, "HEAD") == sha
    for bad in ("nope", "--all"):
        with pytest.raises(PunchError, match=f"unknown revision '{bad}'"):
            resolve_rev(repo, bad)
