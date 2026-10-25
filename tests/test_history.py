"""Issue history: classifying a commit's changes, and `punch history`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from helpers import commit, git, write_raw

from punchlist.cli import main
from punchlist.config import Config, load_config
from punchlist.git import Change
from punchlist.history import Event, Revision, classify, diff, owns, read_revision

PATH = "issues/a/pl-aaaaa.md"
COMMENT = "issues/a/pl-aaaaa/comments/20261019T141205Z-claude.md"


def _issue(body: str = "", **fields: str) -> bytes:
    """An issue file's bytes: required fields, then `fields` (underscores as dashes), then body."""
    header: dict[str, str] = {"id": "pl-aaaaa", "title": "A", "type": "task", "status": "open",
                              **{k.replace("_", "-"): v for k, v in fields.items()},
                              "created": "2026-03-02"}
    text: str = "---\n" + "".join(f"{k}: {v}\n" for k, v in header.items()) + "---\n"
    return (text + "\n" + body if body else text).encode()


def _events(config: Config, old: bytes | None, new: bytes | None,
            change: Change | None = None) -> list[str]:
    status: str = "A" if old is None else "D" if new is None else "M"
    events: list[Event] = classify(change or Change(status, PATH, PATH), old, new, config)
    return [event.describe() for event in events]


def test_created_names_type_and_parent(config: Config) -> None:
    assert _events(config, None, _issue(type="epic")) == ["created (epic)"]
    assert _events(config, None, _issue(parent="pl-bbbbb")) == ["created (task, parent pl-bbbbb)"]
    [event] = classify(Change("A", PATH, PATH), None, _issue(), config)
    assert event.as_json() == {"kind": "created", "header": {
        "id": "pl-aaaaa", "title": "A", "type": "task", "status": "open", "created": "2026-03-02"}}


def test_status_changes(config: Config) -> None:
    assert _events(config, _issue(), _issue(status="closed")) == ["closed"]
    assert _events(config, _issue(status="closed"), _issue()) == ["reopened"]
    [event] = classify(Change("M", PATH, PATH), _issue(), _issue(status="closed"), config)
    assert event.as_json() == {"kind": "closed", "field": "status", "old": "open",
                               "new": "closed"}


def test_field_changes_in_config_order(config: Config) -> None:
    before: bytes = _issue(priority="low", labels="x, y", related="pl-bbbbb")
    after: bytes = _issue(title="B", priority="high", rank="2", labels="y, z")
    assert _events(config, before, after) == [
        "title -> B", "priority low -> high", "rank none -> 2", "related -pl-bbbbb",
        "labels +z -x",
    ]
    assert _events(config, _issue(rank="2"), _issue()) == ["rank 2 -> none"]
    events: list[Event] = classify(Change("M", PATH, PATH), before, after, config)
    assert events[3].as_json() == {"kind": "field", "field": "related", "old": ["pl-bbbbb"],
                                   "new": None}


def test_old_revisions_are_read_leniently(config: Config) -> None:
    # A category and a field today's config doesn't have are still changes worth naming.
    before: bytes = _issue(category="gone", owner="sam")
    after: bytes = _issue(category="tooling")
    assert _events(config, before, after) == ["category gone -> tooling", "owner sam -> none"]


def test_body_counts_lines(config: Config) -> None:
    before: bytes = _issue("one\ntwo\nthree\n")
    assert _events(config, before, _issue("one\n2\nthree\nfour\n")) == ["body +2 -1"]
    assert _events(config, _issue(), _issue("new\n")) == ["body +1 -0"]
    [event] = classify(Change("M", PATH, PATH), before, _issue("one\n"), config)
    assert event.as_json() == {"kind": "body", "added": 0, "removed": 2}
    assert event.as_json(with_diff=True)["diff"] == ["@@ -1,3 +1 @@", " one", "-two", "-three"]


def test_malformed_repaired_reformatted(config: Config) -> None:
    bad: bytes = b"no header\n"
    assert _events(config, None, bad) == ["malformed"]
    assert _events(config, _issue(), bad) == ["malformed"]
    assert _events(config, bad, _issue()) == ["repaired"]
    assert _events(config, _issue(), b"---\nid: x\nid: y\n---\n") == ["malformed"]
    assert _events(config, _issue(), b"\xff") == ["malformed"]
    # The same fields and body with different bytes: a hand reorder, say.
    reordered: bytes = _issue().replace(b"title: A\ntype: task\n", b"type: task\ntitle: A\n")
    assert _events(config, _issue(), reordered) == ["reformatted"]
    assert _events(config, _issue(), _issue().replace(b"\n", b"\r\n")) == ["reformatted"]


def test_moved_and_deleted(config: Config) -> None:
    moved = Change("R", "issues/b/pl-aaaaa.md", PATH)
    assert _events(config, _issue(), _issue(), moved) == ["moved to issues/b/pl-aaaaa.md"]
    assert _events(config, _issue(), _issue(status="closed"), moved) == [
        "moved to issues/b/pl-aaaaa.md", "closed"]
    assert _events(config, _issue(), None) == ["deleted"]


def test_comment_events(config: Config) -> None:
    text: bytes = b"---\nauthor: claude\ncreated: 2026-10-19T14:12:05Z\n---\n\nHi.\n"
    added = Change("A", COMMENT, COMMENT)
    assert _events(config, None, text, added) == ["comment added (claude)"]
    assert _events(config, text, text + b"More.\n", Change("M", COMMENT, COMMENT)) == [
        "comment edited (claude)"]
    assert _events(config, text, None, Change("D", COMMENT, COMMENT)) == [
        "comment deleted (claude)"]
    assert _events(config, None, b"junk", added) == ["comment added (?)"]
    # A comment moved with its issue, unchanged, is the issue file's event to report.
    assert not _events(config, text, text, Change("R", COMMENT, "issues/b/" + COMMENT[9:]))
    [event] = classify(added, None, text, config)
    assert event.as_json() == {"kind": "comment-added", "author": "claude"}


def test_diff_is_empty_for_equal_revisions(config: Config) -> None:
    revision: Revision | None = read_revision(_issue("x\n"), config, PATH)
    assert revision is not None
    assert not diff(revision, revision, config)


def test_owns() -> None:
    assert owns(PATH, "pl-aaaaa")
    assert owns("issues/pl-aaaaa.md", "pl-aaaaa")
    assert owns(COMMENT, "pl-aaaaa")
    assert not owns("issues/a/pl-aaaaab.md", "pl-aaaaa")
    assert not owns("issues/a/pl-aaaab/comments/x.md", "pl-aaaaa")


# --- punch history ---


@pytest.fixture
def repo(root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """`root` as a git repository with its config committed, made the working directory."""
    git(root, "init", "-q")
    commit(root, "Init", "2026-03-01T12:00:00")
    monkeypatch.chdir(root)
    return root


def _run(capsys: pytest.CaptureFixture[str], *args: str) -> str:
    assert main(list(args)) == 0
    return capsys.readouterr().out


def _story(capsys: pytest.CaptureFixture[str], root: Path) -> tuple[str, list[str]]:
    """An issue with a few commits of history; its id and the shas, oldest first."""
    issue_id: str = _run(capsys, "new", "Thing", "--type", "task").strip()
    shas: list[str] = [commit(root, "Add thing", "2026-03-02T12:00:00")]
    _run(capsys, "set", issue_id, "priority=high")
    shas.append(commit(root, f"{issue_id}: prioritize", "2026-03-03T12:00:00"))
    shas.append(commit(root, f"Work for {issue_id}\n\nNot touching it.", "2026-03-04T12:00:00"))
    shas.append(commit(root, f"Not a mention: x{issue_id}", "2026-03-05T12:00:00"))
    _run(capsys, "close", issue_id, "--comment", "Done.", "--author", "claude")
    shas.append(commit(root, "Finish", "2026-03-06T12:00:00"))
    return issue_id, shas


def test_history_timeline(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    issue_id, shas = _story(capsys, repo)
    lines: list[str] = _run(capsys, "history", issue_id[3:]).splitlines()
    assert lines == [
        f"{issue_id} closed task high - Thing",
        f"2026-03-02  {shas[0][:7]}  created (task)                  Add thing",
        f"2026-03-03  {shas[1][:7]}  priority none -> high           {issue_id}: prioritize",
        f"2026-03-04  {shas[2][:7]}  referenced                      Work for {issue_id}",
        f"2026-03-06  {shas[4][:7]}  closed; comment added (claude)  Finish",
    ]


def test_history_since(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    issue_id, shas = _story(capsys, repo)
    by_date: list[str] = _run(capsys, "history", issue_id, "--since", "2026-03-04").splitlines()
    assert [line[12:19] for line in by_date[1:]] == [shas[2][:7], shas[4][:7]]
    by_rev: list[str] = _run(capsys, "history", issue_id, "--since", shas[2][:7]).splitlines()
    assert [line[12:19] for line in by_rev[1:]] == [shas[4][:7]]
    assert main(["history", issue_id, "--since", "nope"]) == 1
    assert "unknown revision 'nope'" in capsys.readouterr().err


def test_history_json(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    issue_id, shas = _story(capsys, repo)
    rows = json.loads(_run(capsys, "history", issue_id, "--json", "--since", shas[1]))
    assert rows == [
        {"id": issue_id, "sha": shas[2], "date": "2026-03-04", "author": "Tester",
         "subject": f"Work for {issue_id}", "events": [{"kind": "referenced"}]},
        {"id": issue_id, "sha": shas[4], "date": "2026-03-06", "author": "Tester",
         "subject": "Finish", "events": [
             {"kind": "closed", "field": "status", "old": "open", "new": "closed"},
             {"kind": "comment-added", "author": "claude"}]},
    ]


def test_history_diff(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    issue_id: str = _run(capsys, "new", "Thing", "--type", "task", "--body", "a\nb\nc").strip()
    commit(repo, "Add")
    path: Path = repo / "issues" / issue_id[3] / f"{issue_id}.md"
    path.write_text(path.read_text(encoding="utf-8").replace("b\n", "B\n"), encoding="utf-8")
    _run(capsys, "set", issue_id, "priority=low")
    sha: str = commit(repo, "Edit", "2026-03-03T12:00:00")
    lines: list[str] = _run(capsys, "history", issue_id, "--diff").splitlines()
    assert lines[2:] == [
        f"2026-03-03  {sha[:7]}  priority none -> low; body +1 -1  Edit",
        "    @@ -1,3 +1,3 @@", "     a", "    -b", "    +B", "     c",
    ]
    rows = json.loads(_run(capsys, "history", issue_id, "--json", "--diff"))
    assert rows[1]["events"][1]["diff"] == ["@@ -1,3 +1,3 @@", " a", "-b", "+B", " c"]
    assert "diff" not in json.loads(_run(capsys, "history", issue_id, "--json"))[1]["events"][1]


def test_history_tree(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    epic: str = _run(capsys, "new", "Epic", "--type", "epic").strip()
    first: str = commit(repo, "Add epic")
    child: str = _run(capsys, "new", "Child", "--type", "task", "--parent", epic).strip()
    grandchild: str = _run(capsys, "new", "Grandchild", "--type", "subtask",
                           "--parent", child).strip()
    _run(capsys, "new", "Unrelated", "--type", "task")
    second: str = commit(repo, "Add children", "2026-03-03T12:00:00")
    third: str = commit(repo, f"Work on {child}", "2026-03-04T12:00:00")
    _run(capsys, "close", grandchild)
    fourth: str = commit(repo, f"Finish {grandchild}", "2026-03-05T12:00:00")

    lines: list[str] = _run(capsys, "history", epic, "--tree").splitlines()
    assert [line.split(" ")[0] for line in lines[:3]] == [epic, child, grandchild]
    widest: int = len(f"created (subtask, parent {child})")
    assert lines[3:] == [
        f"2026-03-02  {first[:7]}  {epic}  {'created (epic)':<{widest}}  Add epic",
        f"2026-03-03  {second[:7]}  {child}  {f'created (task, parent {epic})':<{widest}}  "
        "Add children",
        f"2026-03-03  {second[:7]}  {grandchild}  created (subtask, parent {child})  "
        "Add children",
        f"2026-03-04  {third[:7]}  {child}  {'referenced':<{widest}}  Work on {child}",
        f"2026-03-05  {fourth[:7]}  {grandchild}  {'closed':<{widest}}  Finish {grandchild}",
    ]
    rows = json.loads(_run(capsys, "history", child, "--tree", "--json"))
    assert [(row["id"], row["sha"]) for row in rows] == [
        (child, second), (grandchild, second), (child, third), (grandchild, fourth)]


def test_history_follows_a_move(repo: Path, config: Config,
                                capsys: pytest.CaptureFixture[str]) -> None:
    misplaced: Path = repo / "issues" / "z" / "pl-aaaaa.md"
    misplaced.parent.mkdir(parents=True)
    misplaced.write_bytes(_issue())
    first: str = commit(repo, "Add, in the wrong bucket")
    git(repo, "mv", "issues/z", "issues/a")
    second: str = commit(repo, "Re-bucket", "2026-03-03T12:00:00")
    assert config.issue_path("pl-aaaaa") == repo / PATH
    lines: list[str] = _run(capsys, "history", "pl-aaaaa").splitlines()
    assert [line.split("  ")[1:3] for line in lines[1:]] == [
        [first[:7], "created (task)"], [second[:7], f"moved to {PATH}"]]


def test_history_mention_pattern_from_config(repo: Path,
                                             capsys: pytest.CaptureFixture[str]) -> None:
    config_path: Path = repo / "punchlist.json"
    data = json.loads(config_path.read_text(encoding="utf-8"))
    data["mention_pattern"] = r"(?i)\bfixes {short}\b"
    config_path.write_text(json.dumps(data), encoding="utf-8")
    path: Path = repo / PATH
    path.parent.mkdir(parents=True)
    path.write_bytes(_issue())
    commit(repo, "Add")
    fixed: str = commit(repo, "Fixes aaaaa", "2026-03-03T12:00:00")
    commit(repo, "pl-aaaaa named the default way", "2026-03-04T12:00:00")
    rows = json.loads(_run(capsys, "history", "pl-aaaaa", "--json"))
    assert [(row["sha"], row["events"][0]["kind"]) for row in rows][1:] == [
        (fixed, "referenced")]


def test_history_in_a_subdirectory_of_the_repo(root: Path, monkeypatch: pytest.MonkeyPatch,
                                               capsys: pytest.CaptureFixture[str]) -> None:
    # The tracker lives in sub/; the repository is its parent.
    sub: Path = root / "sub"
    sub.mkdir()
    (root / "punchlist.json").rename(sub / "punchlist.json")
    write_raw(load_config(sub), "pl-aaaaa", _issue())
    git(root, "init", "-q")
    sha: str = commit(root, "Add")
    monkeypatch.chdir(sub)
    lines: list[str] = _run(capsys, "history", "pl-aaaaa").splitlines()
    assert lines[1:] == [f"2026-03-02  {sha[:7]}  created (task)  Add"]


def test_history_outside_git(root: Path, config: Config, monkeypatch: pytest.MonkeyPatch,
                             capsys: pytest.CaptureFixture[str]) -> None:
    write_raw(config, "pl-aaaaa", _issue())
    monkeypatch.chdir(root)
    assert main(["history", "pl-aaaaa"]) == 1
    assert "not in a git repository" in capsys.readouterr().err
