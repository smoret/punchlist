"""Issue times from git and the files, and the orders that use them."""

from __future__ import annotations

import datetime
import os
from pathlib import Path

import pytest
from helpers import commit, git

from punchlist.comment import write_comment
from punchlist.config import CONFIG_NAME, Config, load_config
from punchlist.issue import FieldValue, Issue, load_issues, write_issue
from punchlist.ops import ordered
from punchlist.times import IssueTimes, Times, issue_times, utc_text


def _at(text: str) -> float:
    """Seconds since the epoch of a UTC time like 2026-03-01T10:00:00."""
    return datetime.datetime.fromisoformat(text).replace(tzinfo=datetime.timezone.utc).timestamp()


def _commit(root: Path, message: str, when: str) -> None:
    commit(root, message, when + "+00:00")


def _make(config: Config, issue_id: str, created: str = "2026-03-01") -> Path:
    write_issue(Issue({"id": issue_id, "title": issue_id, "type": "task", "status": "open",
                       "created": created}), config)
    return config.issue_path(issue_id)


def _touch(path: Path, when: str) -> None:
    os.utime(path, (_at(when), _at(when)))


@pytest.fixture
def repo(config: Config) -> Config:
    """`config`'s root as a git repository with no commits yet."""
    git(config.root, "init", "-q")
    return config


def test_times_come_from_commits(repo: Config) -> None:
    _make(repo, "pl-aaaaa")
    _commit(repo.root, "Add", "2026-03-01T10:00:00")
    write_comment(repo, "pl-aaaaa", "alex", "hi\n",
                  datetime.datetime(2026, 3, 5, tzinfo=datetime.timezone.utc))
    _commit(repo.root, "Comment", "2026-03-05T09:30:00")
    times: Times = issue_times(repo)
    assert times.exact
    assert times.by_id["pl-aaaaa"] == IssueTimes(_at("2026-03-01T10:00:00"),
                                                 _at("2026-03-05T09:30:00"))


def test_uncommitted_files_count_from_their_modification_time(repo: Config) -> None:
    edited: Path = _make(repo, "pl-aaaaa")
    _commit(repo.root, "Add", "2026-03-01T10:00:00")
    edited.write_text(edited.read_text(encoding="utf-8") + "more\n", encoding="utf-8")
    _touch(edited, "2026-03-07T08:00:00")
    _touch(_make(repo, "pl-bbbbb"), "2026-03-06T08:00:00")  # never committed
    times: Times = issue_times(repo)
    assert times.by_id["pl-aaaaa"] == IssueTimes(_at("2026-03-01T10:00:00"),
                                                 _at("2026-03-07T08:00:00"))
    assert times.by_id["pl-bbbbb"] == IssueTimes(_at("2026-03-06T08:00:00"),
                                                 _at("2026-03-06T08:00:00"))


def test_a_clean_checkout_ignores_modification_times(repo: Config) -> None:
    path: Path = _make(repo, "pl-aaaaa")
    _commit(repo.root, "Add", "2026-03-01T10:00:00")
    _touch(path, "2026-04-01T00:00:00")  # what a later checkout does to an unchanged file
    assert issue_times(repo).by_id["pl-aaaaa"].updated == _at("2026-03-01T10:00:00")


def test_a_moved_file_keeps_its_creation(repo: Config) -> None:
    path: Path = _make(repo, "pl-aaaaa")
    _commit(repo.root, "Add", "2026-03-01T10:00:00")
    (repo.issues_path / "z").mkdir()
    git(repo.root, "mv", path.relative_to(repo.root).as_posix(), "issues/z/pl-aaaaa.md")
    _commit(repo.root, "Move", "2026-03-02T10:00:00")
    assert issue_times(repo).by_id["pl-aaaaa"] == IssueTimes(_at("2026-03-01T10:00:00"),
                                                             _at("2026-03-02T10:00:00"))


def test_a_tracker_in_a_subdirectory(repo: Config) -> None:
    sub: Path = repo.root / "tracker"
    sub.mkdir()
    (sub / CONFIG_NAME).write_bytes((repo.root / CONFIG_NAME).read_bytes())
    inner: Config = load_config(sub)
    _make(inner, "pl-aaaaa")
    _commit(repo.root, "Add", "2026-03-01T10:00:00")
    assert issue_times(inner).by_id["pl-aaaaa"].created == _at("2026-03-01T10:00:00")


@pytest.mark.parametrize("init", [False, True], ids=["no git", "no commits"])
def test_without_history_every_time_is_a_modification_time(config: Config, init: bool) -> None:
    if init:
        git(config.root, "init", "-q")
    _touch(_make(config, "pl-aaaaa"), "2026-03-04T00:00:00")
    times: Times = issue_times(config)
    assert not times.exact
    assert times.by_id["pl-aaaaa"] == IssueTimes(_at("2026-03-04T00:00:00"),
                                                 _at("2026-03-04T00:00:00"))


def test_utc_text() -> None:
    assert utc_text(_at("2026-10-19T14:12:05")) == "2026-10-19T14:12:05Z"


def _issue(issue_id: str, created: str, **fields: FieldValue) -> Issue:
    return Issue({"id": issue_id, "title": issue_id, "type": "task", "status": "open",
                  "created": created, **fields})


def test_created_order_goes_by_header_date_then_history(config: Config) -> None:
    issues: list[Issue] = [_issue("pl-aaaaa", "2026-03-02"), _issue("pl-bbbbb", "2026-03-02"),
                           _issue("pl-ccccc", "2026-01-01")]  # backdated: history says newest
    times = Times({"pl-aaaaa": IssueTimes(_at("2026-03-02T08:00:00"), 0),
                   "pl-bbbbb": IssueTimes(_at("2026-03-02T09:00:00"), 0),
                   "pl-ccccc": IssueTimes(_at("2026-03-09T00:00:00"), 0)}, True)
    assert [i.id for i in ordered(config, issues, "created", times)] == [
        "pl-bbbbb", "pl-aaaaa", "pl-ccccc"]


def test_updated_order_is_newest_first(config: Config) -> None:
    issues: list[Issue] = [_issue("pl-aaaaa", "2026-03-01", priority="high"),
                           _issue("pl-bbbbb", "2026-03-01", priority="low")]
    times = Times({"pl-aaaaa": IssueTimes(0, _at("2026-03-02T00:00:00")),
                   "pl-bbbbb": IssueTimes(0, _at("2026-03-03T00:00:00"))}, True)
    assert [i.id for i in ordered(config, issues, "updated", times)] == ["pl-bbbbb", "pl-aaaaa"]
    assert [i.id for i in ordered(config, issues, "rank")] == ["pl-aaaaa", "pl-bbbbb"]


def test_a_time_order_needs_times(config: Config) -> None:
    with pytest.raises(ValueError, match="needs issue times"):
        ordered(config, [], "updated")


def test_every_loaded_issue_has_times(repo: Config) -> None:
    for issue_id in ("pl-aaaaa", "pl-bbbbb"):
        _make(repo, issue_id)
    _commit(repo.root, "Add", "2026-03-01T10:00:00")
    _make(repo, "pl-ccccc")
    assert set(issue_times(repo).by_id) == {i.id for i in load_issues(repo)}
