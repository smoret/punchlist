"""Comments: the file format, naming, writing, check, and the CLI."""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest
from helpers import write_raw

from punchlist import cli
from punchlist.check import check_issues
from punchlist.cli import main
from punchlist.comment import (
    Comment, author_slug, format_comment, load_comments, parse_comment, timestamp, write_comment,
)
from punchlist.config import Config
from punchlist.errors import PunchError
from punchlist.issue import Issue, write_issue

WHEN = datetime.datetime(2026, 10, 19, 14, 12, 5, tzinfo=datetime.timezone.utc)
COMMENTS = "issues/a/pl-aaaaa/comments"


@pytest.fixture
def issue(config: Config) -> Config:
    """`config` with one issue, pl-aaaaa."""
    write_issue(Issue({"id": "pl-aaaaa", "title": "T", "type": "bug", "status": "open",
                       "created": "2026-01-01"}, "Body.\n"), config)
    return config


@pytest.mark.parametrize(
    ("author", "slug"),
    [("claude", "claude"), ("Steve Moret", "steve-moret"), (" A.B_c! ", "a-b-c"),
     ("Zoë", "zo")],
)
def test_author_slug(author: str, slug: str) -> None:
    assert author_slug(author) == slug


def test_author_slug_needs_letters() -> None:
    with pytest.raises(PunchError, match="no letters or digits"):
        author_slug("!!!")


def test_timestamp_is_utc() -> None:
    pacific = datetime.timezone(datetime.timedelta(hours=-7))
    assert timestamp(datetime.datetime(2026, 10, 19, 7, 12, 5, tzinfo=pacific)) == (
        "2026-10-19T14:12:05Z"
    )


def test_write_and_read(issue: Config) -> None:
    path = write_comment(issue, "pl-aaaaa", "Steve Moret", "Tried X.\n", WHEN)
    assert path.relative_to(issue.root).as_posix() == (
        f"{COMMENTS}/20261019T141205Z-steve-moret.md"
    )
    assert path.read_text(encoding="utf-8") == (
        "---\nauthor: Steve Moret\ncreated: 2026-10-19T14:12:05Z\n---\n\nTried X.\n"
    )
    assert load_comments(issue, "pl-aaaaa") == [
        Comment("Steve Moret", "2026-10-19T14:12:05Z", "Tried X.\n", path, 1)
    ]


def test_same_second_same_author_never_overwrites(issue: Config) -> None:
    paths = [write_comment(issue, "pl-aaaaa", "claude", f"Number {n}.\n", WHEN)
             for n in range(1, 4)]
    assert [p.name for p in paths] == [
        "20261019T141205Z-claude.md", "20261019T141205Z-claude-2.md",
        "20261019T141205Z-claude-3.md",
    ]
    # Oldest first: -2 sorts after the plain name although '-' < '.' in a filename sort.
    assert [c.body for c in load_comments(issue, "pl-aaaaa")] == [
        "Number 1.\n", "Number 2.\n", "Number 3.\n",
    ]


def test_load_orders_by_time(issue: Config) -> None:
    later = WHEN + datetime.timedelta(seconds=1)
    write_comment(issue, "pl-aaaaa", "zed", "Second.\n", later)
    write_comment(issue, "pl-aaaaa", "amy", "First.\n", WHEN)
    assert [c.body for c in load_comments(issue, "pl-aaaaa")] == ["First.\n", "Second.\n"]


def test_no_comments(issue: Config) -> None:
    assert not load_comments(issue, "pl-aaaaa")


GOOD = "---\nauthor: claude\ncreated: 2026-10-19T14:12:05Z\n---\n\nText.\n"
NAME = Path("20261019T141205Z-claude.md")


def test_format_round_trips() -> None:
    assert format_comment(parse_comment(GOOD, NAME)) == GOOD


@pytest.mark.parametrize(
    ("text", "name", "message"),
    [
        (GOOD.replace("author", "who"), NAME, "unknown comment field 'who'"),
        (GOOD.replace("author: claude\n", ""), NAME, "missing required field.*author"),
        (GOOD.replace("claude", " "), NAME, "empty value for 'author'"),
        (GOOD + "", NAME.with_name("x.md"), "filename does not match the header; "
                                            "expected 20261019T141205Z-claude.md"),
        (GOOD.replace("Z\n", "\n"), NAME, "not a UTC time"),
        (GOOD.replace("14:12", "25:12"), NAME, "not a UTC time"),
        (GOOD.replace("Text.\n", "  \n"), NAME, "comment has no body"),
        (GOOD.replace("author: claude", "author: claude\nauthor: amy"), NAME, "duplicate field"),
        ("no header\n", NAME, "first line must be '---'"),
    ],
)
def test_parse_rejects(text: str, name: Path, message: str) -> None:
    with pytest.raises(PunchError, match=message):
        parse_comment(text, name)


@pytest.mark.parametrize("suffix", ["-2", "-10"])
def test_sequence_suffix_accepted(suffix: str) -> None:
    assert parse_comment(GOOD, Path(f"20261019T141205Z-claude{suffix}.md")).sequence == int(
        suffix[1:]
    )


@pytest.mark.parametrize("suffix", ["-1", "-02", "-x", "-2-3"])
def test_sequence_suffix_rejected(suffix: str) -> None:
    with pytest.raises(PunchError, match="filename does not match"):
        parse_comment(GOOD, Path(f"20261019T141205Z-claude{suffix}.md"))


def _check(config: Config) -> list[str]:
    return [p.describe(config.root) for p in check_issues(config)[1]]


def test_check_accepts_comments(issue: Config) -> None:
    write_comment(issue, "pl-aaaaa", "claude", "Fine.\n", WHEN)
    assert not _check(issue)


def test_check_reports_comment_problems(issue: Config) -> None:
    comments: Path = issue.comments_path("pl-aaaaa")
    comments.mkdir(parents=True)
    (comments / "20261019T141205Z-claude.md").write_text("junk\n", encoding="utf-8")
    (comments / "20261019T141205Z-amy.md").write_text(GOOD, encoding="utf-8")  # author claude
    (comments / "notes.txt").write_text("x", encoding="utf-8")
    (comments / "sub").mkdir()
    assert _check(issue) == [
        f"{COMMENTS}/20261019T141205Z-amy.md: filename does not match the header; "
        "expected 20261019T141205Z-claude.md",
        f"{COMMENTS}/20261019T141205Z-claude.md:1: first line must be '---'",
        f"{COMMENTS}/notes.txt: not a comment file (expected <time>-<author>.md)",
        f"{COMMENTS}/sub: not a comment file (expected <time>-<author>.md)",
    ]


def test_check_reports_orphan_folders(issue: Config) -> None:
    (issue.issues_path / "z" / "pl-zzzzz" / "comments").mkdir(parents=True)  # no such issue
    (issue.issues_path / "b" / "pl-aaaaa").mkdir(parents=True)  # issue lives in bucket a
    (issue.issues_path / "a" / "notes").mkdir()  # not an id at all
    assert _check(issue) == [
        "issues/a/notes: folder for issue 'notes', which does not exist here",
        "issues/b/pl-aaaaa: folder for issue 'pl-aaaaa', which does not exist here",
        "issues/z/pl-zzzzz: folder for issue 'pl-zzzzz', which does not exist here",
    ]


@pytest.fixture
def in_issue(issue: Config, monkeypatch: pytest.MonkeyPatch) -> Config:
    """`issue`, as the working directory, with no PUNCH_AUTHOR and no git user."""
    monkeypatch.chdir(issue.root)
    monkeypatch.delenv("PUNCH_AUTHOR", raising=False)
    monkeypatch.setattr(cli, "git_user", lambda _root: None)
    return issue


def _comment(capsys: pytest.CaptureFixture[str], *args: str) -> str:
    assert main(["comment", *args]) == 0
    return capsys.readouterr().out.strip()


def test_cli_comment_author_flag(in_issue: Config, capsys: pytest.CaptureFixture[str]) -> None:
    # Blank lines around the body are trimmed, as for `new`; spaces within a line are kept.
    shown = _comment(capsys, "aaaaa", "--author", "claude", "--body", "\n\n  Tried X.\n\n")
    assert shown.startswith(f"{COMMENTS}/") and shown.endswith("-claude.md")
    [comment] = load_comments(in_issue, "pl-aaaaa")
    assert (comment.author, comment.body) == ("claude", "  Tried X.\n")


def test_cli_author_precedence(in_issue: Config, monkeypatch: pytest.MonkeyPatch,
                               capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli, "git_user", lambda _root: "Git User")
    assert _comment(capsys, "aaaaa", "--body", "x").endswith("-git-user.md")
    monkeypatch.setenv("PUNCH_AUTHOR", "env-bot")
    assert _comment(capsys, "aaaaa", "--body", "x").endswith("-env-bot.md")
    assert _comment(capsys, "aaaaa", "--author", "Flag", "--body", "x").endswith("-flag.md")


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["aaaaa", "--body", "x"], "no author: pass --author, set PUNCH_AUTHOR, or set git's"),
        (["aaaaa", "--author", "a"], "comment body is empty"),
        (["aaaaa", "--author", "a", "--body", "  "], "comment body is empty"),
        (["aaaaa", "--author", "!!", "--body", "x"], "no letters or digits"),
        (["zzz", "--author", "a", "--body", "x"], "no issue matches 'zzz'"),
    ],
)
def test_cli_comment_rejects(in_issue: Config, capsys: pytest.CaptureFixture[str],
                             args: list[str], message: str) -> None:
    assert main(["comment", *args]) == 1
    assert message in capsys.readouterr().err
    assert not in_issue.comments_path("pl-aaaaa").exists()


def test_cli_comment_on_corrupt_issue(in_issue: Config, capsys: pytest.CaptureFixture[str]) -> None:
    write_raw(in_issue, "pl-bbbbb", "junk\n")
    assert main(["comment", "bbbbb", "--author", "a", "--body", "x"]) == 1
    assert "pl-bbbbb.md:1: first line must be '---'" in capsys.readouterr().err


def test_cli_show_comments(in_issue: Config, capsys: pytest.CaptureFixture[str]) -> None:
    write_comment(in_issue, "pl-aaaaa", "claude", "Tried X.\n", WHEN)
    write_comment(in_issue, "pl-aaaaa", "Steve", "Try Y.\n", WHEN + datetime.timedelta(hours=1))
    plain: str = in_issue.issue_path("pl-aaaaa").read_text(encoding="utf-8")
    assert main(["show", "aaaaa"]) == 0
    assert capsys.readouterr().out == plain  # unchanged without --comments
    assert main(["show", "aaaaa", "--comments"]) == 0
    assert capsys.readouterr().out == plain + (
        "\n## Comments\n"
        "\n### claude, 2026-10-19T14:12:05Z\n\nTried X.\n"
        "\n### Steve, 2026-10-19T15:12:05Z\n\nTry Y.\n"
    )
    assert main(["show", "aaaaa", "--comments", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["comments"] == [
        {"author": "claude", "created": "2026-10-19T14:12:05Z", "body": "Tried X.\n"},
        {"author": "Steve", "created": "2026-10-19T15:12:05Z", "body": "Try Y.\n"},
    ]
    assert main(["show", "aaaaa", "--json"]) == 0
    assert "comments" not in json.loads(capsys.readouterr().out)


def test_cli_show_comments_none(in_issue: Config, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["show", "aaaaa", "--comments"]) == 0
    assert "## Comments" not in capsys.readouterr().out


def test_close_with_comment(in_issue: Config, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["close", "aaaaa", "--comment", "Fixed by the retry change.",
                 "--author", "claude"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith(f"{COMMENTS}/") and lines[0].endswith("-claude.md")
    assert lines[1] == "closed pl-aaaaa"
    [comment] = load_comments(in_issue, "pl-aaaaa")
    assert (comment.author, comment.body) == ("claude", "Fixed by the retry change.\n")
    assert "status: closed\n" in in_issue.issue_path("pl-aaaaa").read_text(encoding="utf-8")


def test_close_comment_author_falls_back(in_issue: Config, monkeypatch: pytest.MonkeyPatch,
                                         capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("PUNCH_AUTHOR", "env-bot")
    assert main(["close", "aaaaa", "--comment", "Done."]) == 0
    assert capsys.readouterr().out.splitlines()[0].endswith("-env-bot.md")


def test_close_again_does_not_repeat_the_comment(in_issue: Config,
                                                 capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["close", "aaaaa", "--comment", "Done.", "--author", "a"]) == 0
    capsys.readouterr()
    assert main(["close", "aaaaa", "--comment", "Done.", "--author", "a"]) == 0
    assert capsys.readouterr().out == "pl-aaaaa already closed; comment not added\n"
    assert len(load_comments(in_issue, "pl-aaaaa")) == 1


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--comment", "x"], "no author: pass --author"),  # nothing in the env, no git user
        (["--comment", "\n \n", "--author", "a"], "--comment is empty"),
        (["--author", "a"], "--author only applies with --comment"),
    ],
)
def test_close_comment_rejects_leaves_issue_open(in_issue: Config,
                                                 capsys: pytest.CaptureFixture[str],
                                                 args: list[str], message: str) -> None:
    assert main(["close", "aaaaa", *args]) == 1
    assert message in capsys.readouterr().err
    assert "status: open\n" in in_issue.issue_path("pl-aaaaa").read_text(encoding="utf-8")
    assert not load_comments(in_issue, "pl-aaaaa")
