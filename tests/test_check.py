"""`punch check`: every problem in every file, one line each."""

from __future__ import annotations

from pathlib import Path

import pytest
from helpers import write_raw

from punchlist.check import check_issues
from punchlist.cli import main
from punchlist.config import Config
from punchlist.issue import Issue, write_issue

HEADER = "---\nid: {id}\ntitle: T\ntype: bug\nstatus: open\n{extra}created: 2026-01-01\n---\n"


def _good(config: Config, stem: str, extra: str = "") -> None:
    write_raw(config, stem, HEADER.format(id=stem, extra=extra))


def _lines(config: Config) -> list[str]:
    _count, problems = check_issues(config)
    return [problem.describe(config.root) for problem in problems]


def test_no_issues_dir(config: Config) -> None:
    assert check_issues(config) == (0, [])


def test_clean(config: Config) -> None:
    write_issue(Issue({"id": "pl-aaaaa", "title": "T", "type": "epic", "status": "open",
                       "created": "2026-01-01"}), config)
    _good(config, "pl-bbbbb", "parent: pl-aaaaa\n")
    assert check_issues(config) == (2, [])


def test_reports_every_problem_in_file_order(config: Config) -> None:
    _good(config, "pl-ok001")
    write_raw(config, "pl-bad01", "no header\n")
    _good(config, "pl-bad02", "priority: urgent\n")
    write_raw(config, "pl-bad03", HEADER.format(id="pl-other", extra=""))
    _good(config, "pl-bad04", "parent: pl-zzzzz\n")
    assert _lines(config) == [
        "issues/b/pl-bad01.md:1: first line must be '---'",
        "issues/b/pl-bad02.md:6: priority 'urgent' is not one of: high, medium, low",
        "issues/b/pl-bad03.md:2: id 'pl-other' does not match the filename",
        "issues/b/pl-bad04.md:6: parent 'pl-zzzzz' does not exist",
    ]


def test_id_and_parent_problems_in_one_file(config: Config) -> None:
    write_raw(config, "pl-bad01", HEADER.format(id="pl-other", extra="parent: pl-zzzzz\n"))
    assert _lines(config) == [
        "issues/b/pl-bad01.md:2: id 'pl-other' does not match the filename",
        "issues/b/pl-bad01.md:6: parent 'pl-zzzzz' does not exist",
    ]


def test_corrupt_parent_is_one_problem(config: Config) -> None:
    write_raw(config, "pl-epic1", "junk\n")
    _good(config, "pl-kid01", "parent: pl-epic1\n")
    _good(config, "pl-kid02", "parent: pl-epic1\n")
    assert _lines(config) == ["issues/e/pl-epic1.md:1: first line must be '---'"]


def test_wrong_bucket(config: Config) -> None:
    _good(config, "pl-aaaaa")
    stray: Path = config.issues_path / "z" / "pl-bbbbb.md"
    stray.parent.mkdir()
    stray.write_text(HEADER.format(id="pl-bbbbb", extra="parent: pl-aaaaa\n"), encoding="utf-8")
    assert _lines(config) == [
        "issues/z/pl-bbbbb.md: in the wrong bucket; it belongs at issues/b/pl-bbbbb.md",
    ]


def test_flat_file_is_in_the_wrong_bucket(config: Config) -> None:
    config.issues_path.mkdir()
    (config.issues_path / "pl-aaaaa.md").write_text(HEADER.format(id="pl-aaaaa", extra=""),
                                                    encoding="utf-8")
    assert _lines(config) == [
        "issues/pl-aaaaa.md: in the wrong bucket; it belongs at issues/a/pl-aaaaa.md",
    ]


def test_misplaced_file_is_still_parsed(config: Config) -> None:
    config.issues_path.mkdir()
    (config.issues_path / "pl-aaaaa.md").write_text("junk\n", encoding="utf-8")
    assert _lines(config) == [
        "issues/pl-aaaaa.md: in the wrong bucket; it belongs at issues/a/pl-aaaaa.md",
        "issues/pl-aaaaa.md:1: first line must be '---'",
    ]


def test_filename_with_no_bucket(config: Config) -> None:
    config.issues_path.mkdir()
    (config.issues_path / "notes.md").write_text("junk\n", encoding="utf-8")
    assert _lines(config)[0] == (
        "issues/notes.md: id 'notes' needs a '-' and at least 1 characters after it"
    )


def test_self_parent(config: Config) -> None:
    _good(config, "pl-aaaaa", "parent: pl-aaaaa\n")
    assert _lines(config) == ["issues/a/pl-aaaaa.md:6: parent loop: pl-aaaaa -> pl-aaaaa"]


def test_loop_reported_once_at_lowest_id(config: Config) -> None:
    _good(config, "pl-ccccc", "parent: pl-aaaaa\n")
    _good(config, "pl-aaaaa", "parent: pl-bbbbb\n")
    _good(config, "pl-bbbbb", "parent: pl-ccccc\n")
    _good(config, "pl-ddddd", "parent: pl-bbbbb\n")  # hangs off the loop; not itself a loop
    assert _lines(config) == [
        "issues/a/pl-aaaaa.md:6: parent loop: pl-aaaaa -> pl-bbbbb -> pl-ccccc -> pl-aaaaa",
    ]


def test_two_loops_and_a_chain(config: Config) -> None:
    _good(config, "pl-aaaaa", "parent: pl-bbbbb\n")
    _good(config, "pl-bbbbb", "parent: pl-aaaaa\n")
    _good(config, "pl-ccccc", "parent: pl-ccccc\n")
    _good(config, "pl-ddddd")
    _good(config, "pl-eeeee", "parent: pl-ddddd\n")
    _good(config, "pl-fffff", "parent: pl-eeeee\n")
    assert _lines(config) == [
        "issues/a/pl-aaaaa.md:6: parent loop: pl-aaaaa -> pl-bbbbb -> pl-aaaaa",
        "issues/c/pl-ccccc.md:6: parent loop: pl-ccccc -> pl-ccccc",
    ]


def test_loop_through_corrupt_file_is_not_reported(config: Config) -> None:
    # The corrupt file is the problem; its parent is unknown, so no loop is claimed.
    _good(config, "pl-aaaaa", "parent: pl-bbbbb\n")
    write_raw(config, "pl-bbbbb", "junk\n")
    assert _lines(config) == ["issues/b/pl-bbbbb.md:1: first line must be '---'"]


def test_invalid_utf8_names_line(config: Config) -> None:
    write_raw(config, "pl-bad01", b"---\nid: pl-bad01\ntitle: caf\xe9\n")
    assert _lines(config) == ["issues/b/pl-bad01.md:3: not valid UTF-8"]


def test_ignores_non_issue_files(config: Config) -> None:
    _good(config, "pl-ok001")
    (config.issues_path / "notes.txt").write_text("junk", encoding="utf-8")
    (config.issues_path / "o" / "pl-ok001" / "comments").mkdir(parents=True)
    (config.issues_path / "o" / "pl-ok001" / "comments" / "c.md").write_text("x", encoding="utf-8")
    assert check_issues(config) == (1, [])


def test_cli_ok(root: Path, config: Config, monkeypatch: pytest.MonkeyPatch,
                capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.chdir(root)
    _good(config, "pl-ok001")
    assert main(["check"]) == 0
    assert capsys.readouterr().out == "1 issue ok\n"
    _good(config, "pl-ok002")
    assert main(["check"]) == 0
    assert capsys.readouterr().out == "2 issues ok\n"


def test_cli_problems(root: Path, config: Config, monkeypatch: pytest.MonkeyPatch,
                      capsys: pytest.CaptureFixture[str]) -> None:
    (root / "sub").mkdir()
    monkeypatch.chdir(root / "sub")
    _good(config, "pl-ok001")
    write_raw(config, "pl-bad01", "junk\n")
    _good(config, "pl-bad02", "parent: pl-zzzzz\n")
    assert main(["check"]) == 1
    # Paths are shown relative to the working directory when under it; here they aren't.
    issues: str = config.issues_path.resolve().as_posix()
    assert capsys.readouterr().out.splitlines() == [
        f"{issues}/b/pl-bad01.md:1: first line must be '---'",
        f"{issues}/b/pl-bad02.md:6: parent 'pl-zzzzz' does not exist",
    ]


def test_cli_paths_relative_to_cwd(root: Path, config: Config, monkeypatch: pytest.MonkeyPatch,
                                   capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.chdir(root)
    write_raw(config, "pl-bad01", "junk\n")
    assert main(["check"]) == 1
    assert capsys.readouterr().out == "issues/b/pl-bad01.md:1: first line must be '---'\n"
