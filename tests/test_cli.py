"""The `punch` command line, driven through main()."""

from __future__ import annotations

import datetime
import io
import json
from pathlib import Path

import pytest
from helpers import write_raw

from punchlist.cli import main
from punchlist.config import CONFIG_NAME, Config, default_config, load_config
from punchlist.issue import FieldValue, Issue, read_issue, write_issue


def test_init_writes_loadable_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                     capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["init", "--prefix", "pl"]) == 0
    assert load_config(tmp_path).prefix == "pl"
    assert '\n  "fanout": 1,\n' in (tmp_path / CONFIG_NAME).read_text(encoding="utf-8")
    assert capsys.readouterr().out.startswith("wrote ")


@pytest.mark.parametrize(("choice", "eol"), [("lf", b"\n"), ("crlf", b"\r\n")])
def test_init_line_endings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                           choice: str, eol: bytes) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["init", "--prefix", "pl", "--line-endings", choice]) == 0
    assert load_config(tmp_path).line_endings == choice
    assert _only_endings((tmp_path / CONFIG_NAME).read_bytes(), eol)


def _only_endings(raw: bytes, eol: bytes) -> bool:
    """Whether `raw` has line breaks and every one is `eol`."""
    rest: bytes = raw.replace(eol, b"")
    return len(rest) < len(raw) and b"\r" not in rest and b"\n" not in rest


@pytest.mark.parametrize(("choice", "eol"), [("lf", b"\n"), ("crlf", b"\r\n")])
def test_every_write_uses_line_endings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                       capsys: pytest.CaptureFixture[str],
                                       choice: str, eol: bytes) -> None:
    # new, set (one file and a related pair), comment, close, and import's category rewrite.
    data = default_config("pl")
    data["line_endings"] = choice
    (tmp_path / CONFIG_NAME).write_text(json.dumps(data, indent=2), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    first = _new(capsys, "First", "--type", "task", "--body", "One.\n\nTwo.")
    second = _new(capsys, "Second", "--type", "task", "--related", first)
    assert main(["set", first, "priority=high"]) == 0
    assert main(["comment", first, "--author", "claude", "--body", "A\nB"]) == 0
    assert main(["close", second, "--comment", "Done.", "--author", "claude"]) == 0
    (tmp_path / "ROADMAP.md").write_text("## Docs\n\n- [ ] **T**\n", encoding="utf-8")
    assert main(["import-md", "ROADMAP.md", "--add-categories"]) == 0
    written: list[Path] = [tmp_path / CONFIG_NAME,
                           *(p for p in (tmp_path / "issues").rglob("*.md"))]
    assert len(written) == 6  # config, three issues, two comments
    for path in written:
        assert _only_endings(path.read_bytes(), eol), path


def test_init_refuses_existing(root: Path, monkeypatch: pytest.MonkeyPatch,
                               capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.chdir(root)
    before: str = (root / CONFIG_NAME).read_text(encoding="utf-8")
    assert main(["init", "--prefix", "pl"]) == 1
    assert "already exists" in capsys.readouterr().err
    assert (root / CONFIG_NAME).read_text(encoding="utf-8") == before


def test_init_rejects_bad_prefix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                 capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["init", "--prefix", "P-L"]) == 1
    assert "lowercase" in capsys.readouterr().err
    assert not (tmp_path / CONFIG_NAME).exists()


@pytest.fixture
def in_root(root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """`root`, made the working directory."""
    monkeypatch.chdir(root)
    return root


def _new(capsys: pytest.CaptureFixture[str], *args: str) -> str:
    """Run `punch new`, assert it succeeded, and return the printed id."""
    assert main(["new", *args]) == 0
    out: str = capsys.readouterr().out
    assert out.count("\n") == 1
    return out.strip()


def test_new_minimal(in_root: Path, config: Config, capsys: pytest.CaptureFixture[str]) -> None:
    issue_id = _new(capsys, "Fix the thing", "--type", "bug")
    path = config.issue_path(issue_id)
    assert path.parent == config.issues_path / issue_id[3]
    assert path.read_text(encoding="utf-8") == (
        f"---\nid: {issue_id}\ntitle: Fix the thing\ntype: bug\nstatus: open\n"
        f"created: {datetime.date.today().isoformat()}\n---\n"
    )


def test_new_all_fields(in_root: Path, config: Config, capsys: pytest.CaptureFixture[str]) -> None:
    parent = _new(capsys, "Epic", "--type", "epic")
    child = _new(capsys, "  Child  ", "--type", "task", "--priority", "high",
                 "--category", "docs", "--parent", parent,
                 "--label", "a, b", "--label", "c", "--body", "\n\nLine one.\n\n")
    issue = read_issue(config.issue_path(child), config)
    assert issue.title == "Child"
    assert issue.text("priority") == "high"
    assert issue.text("category") == "docs"
    assert issue.text("parent") == parent
    assert issue.items("labels") == ["a", "b", "c"]
    assert issue.body == "Line one.\n"


def test_new_found_from_subdirectory(in_root: Path, config: Config, monkeypatch: pytest.MonkeyPatch,
                                     capsys: pytest.CaptureFixture[str]) -> None:
    (in_root / "sub").mkdir()
    monkeypatch.chdir(in_root / "sub")
    issue_id = _new(capsys, "T", "--type", "bug")
    assert config.issue_path(issue_id).is_file()


def test_new_body_file(in_root: Path, config: Config, capsys: pytest.CaptureFixture[str]) -> None:
    (in_root / "body.md").write_text("From a file — UTF-8.\n", encoding="utf-8")
    issue_id = _new(capsys, "T", "--type", "bug", "--body-file", "body.md")
    assert read_issue(config.issue_path(issue_id), config).body == "From a file — UTF-8.\n"


def test_new_body_stdin(in_root: Path, config: Config, monkeypatch: pytest.MonkeyPatch,
                        capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("From stdin.\n"))
    issue_id = _new(capsys, "T", "--type", "bug", "--body-file", "-")
    assert read_issue(config.issue_path(issue_id), config).body == "From stdin.\n"


def test_new_blank_body_is_empty(in_root: Path, config: Config, capsys: pytest.CaptureFixture[str]) -> None:
    issue_id = _new(capsys, "T", "--type", "bug", "--body", "\n  \n")
    assert read_issue(config.issue_path(issue_id), config).body == ""


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["T"], "required: --type"),
        (["T", "--type", "nope"], "type 'nope' is not one of"),
        (["T", "--type", "bug", "--priority", "urgent"], "priority 'urgent'"),
        (["T", "--type", "bug", "--category", "web"], "category 'web'"),
        (["T", "--type", "bug", "--parent", "pl-zzzzz"], "no issue matches 'pl-zzzzz'"),
        (["T", "--type", "bug", "--label", "a,,b"], "empty label"),
        (["   ", "--type", "bug"], "title must not be empty"),
        (["a\nb", "--type", "bug"], "single line"),
        (["T", "--type", "bug", "--body-file", "missing.md"], "cannot read body file"),
        (["T", "--type", "bug", "--body", "x", "--body-file", "y"], "not allowed with"),
    ],
)
def test_new_rejects(in_root: Path, config: Config, capsys: pytest.CaptureFixture[str],
                     args: list[str], message: str) -> None:
    try:
        status = main(["new", *args])
    except SystemExit as exc:  # argparse usage errors
        status = exc.code
    assert status != 0
    assert message in capsys.readouterr().err
    assert not config.issues_path.exists() or not any(config.issues_path.iterdir())


def test_new_outside_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                          capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["new", "T", "--type", "bug"]) == 1
    assert "run `punch init`" in capsys.readouterr().err


def _make(config: Config, issue_id: str, title: str, **fields: FieldValue) -> None:
    """Write an issue with a fixed id; type task, status open, created 2026-01-01 unless given."""
    base: dict[str, FieldValue] = {
        "id": issue_id, "title": title, "type": "task", "status": "open", "created": "2026-01-01",
    }
    write_issue(Issue({**base, **fields}), config)


@pytest.fixture
def sample(in_root: Path, config: Config) -> Config:
    """Five issues covering every filter."""
    _make(config, "pl-epic1", "The epic", type="epic", priority="medium", category="tooling")
    _make(config, "pl-low01", "Low child", priority="low", parent="pl-epic1", labels=["a", "b"])
    _make(config, "pl-high1", "High one — dash", priority="high", category="docs", labels=["a"])
    _make(config, "pl-none1", "No priority", type="bug", created="2025-12-31")
    _make(config, "pl-done1", "Closed one", status="closed", priority="high")
    return config


def _list(capsys: pytest.CaptureFixture[str], *args: str) -> list[str]:
    """Run `punch list`, assert it succeeded, and return the ids printed."""
    assert main(["list", *args]) == 0
    return [line.split(" ", 1)[0] for line in capsys.readouterr().out.splitlines()]


def test_list_line_format(sample: Config, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["list", "--priority", "high"]) == 0
    assert capsys.readouterr().out == "pl-high1 open task high docs High one — dash\n"
    assert main(["list", "--type", "bug"]) == 0
    assert capsys.readouterr().out == "pl-none1 open bug - - No priority\n"


def test_list_open_sorted_by_priority(sample: Config, capsys: pytest.CaptureFixture[str]) -> None:
    # Priority in config order, missing priority last.
    assert _list(capsys) == ["pl-high1", "pl-epic1", "pl-low01", "pl-none1"]


def test_list_default_includes_every_status_but_closed(
        in_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = default_config("pl")
    data["statuses"] = ["open", "progress", "closed"]
    (in_root / CONFIG_NAME).write_text(json.dumps(data), encoding="utf-8")
    config: Config = load_config(in_root)
    _make(config, "pl-open1", "Open")
    _make(config, "pl-prog1", "Moving", status="progress")
    _make(config, "pl-done1", "Done", status="closed")
    assert _list(capsys) == ["pl-open1", "pl-prog1"]
    assert _list(capsys, "--status", "progress") == ["pl-prog1"]
    assert main(["search", "o", "--json"]) == 0
    assert [row["id"] for row in json.loads(capsys.readouterr().out)] == ["pl-open1", "pl-prog1"]


def test_list_sorts_by_created_within_priority(sample: Config, capsys: pytest.CaptureFixture[str]) -> None:
    _make(sample, "pl-none0", "Newer, no priority", created="2026-02-01")
    assert _list(capsys, "--priority", "high", "--all")[0] == "pl-done1"
    assert _list(capsys)[-2:] == ["pl-none1", "pl-none0"]


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["--status", "closed"], ["pl-done1"]),
        (["--all"], ["pl-done1", "pl-high1", "pl-epic1", "pl-low01", "pl-none1"]),
        (["--type", "epic"], ["pl-epic1"]),
        (["--category", "docs"], ["pl-high1"]),
        (["--parent", "pl-epic1"], ["pl-low01"]),
        (["--label", "a"], ["pl-high1", "pl-low01"]),
        (["--label", "a", "--label", "b"], ["pl-low01"]),
        (["--priority", "high", "--all"], ["pl-done1", "pl-high1"]),
        (["--category", "tooling", "--type", "bug"], []),
    ],
)
def test_list_filters(sample: Config, capsys: pytest.CaptureFixture[str],
                      args: list[str], expected: list[str]) -> None:
    assert _list(capsys, *args) == expected


def test_list_json(sample: Config, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["list", "--label", "b", "--json"]) == 0
    out: str = capsys.readouterr().out
    assert out == (
        '[{"id": "pl-low01", "title": "Low child", "type": "task", "status": "open", '
        '"priority": "low", "parent": "pl-epic1", "labels": ["a", "b"], "created": "2026-01-01"}]\n'
    )


def test_list_empty(in_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _list(capsys) == []
    assert main(["list", "--json"]) == 0
    assert capsys.readouterr().out == "[]\n"


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--status", "done"], "status 'done' is not one of"),
        (["--type", "tsak"], "type 'tsak'"),
        (["--category", "web"], "category 'web'"),
        (["--parent", "pl-zzzzz"], "no issue matches 'pl-zzzzz'"),
        (["--parent", "pl-"], "ambiguous"),
    ],
)
def test_list_rejects(sample: Config, capsys: pytest.CaptureFixture[str],
                      args: list[str], message: str) -> None:
    assert main(["list", *args]) == 1
    assert message in capsys.readouterr().err


def test_list_fails_closed_on_bad_file(sample: Config, capsys: pytest.CaptureFixture[str]) -> None:
    write_raw(sample, "pl-bad01", "---\nid: pl-bad01\n")
    assert main(["list"]) == 1
    err: str = capsys.readouterr().err
    assert "issues/b/pl-bad01.md:1: header has no closing" in err
    assert err.rstrip().endswith("(run `punch check` to find corrupt files)")


def test_list_fails_closed_on_misplaced_file(sample: Config, capsys: pytest.CaptureFixture[str]) -> None:
    sample.issue_path("pl-high1").rename(sample.issues_path / "pl-high1.md")
    assert main(["list"]) == 1
    err: str = capsys.readouterr().err
    assert "issues/pl-high1.md: in the wrong bucket; it belongs at issues/h/pl-high1.md" in err
    assert err.rstrip().endswith("(run `punch check` to find corrupt files)")


def test_parent_accepts_short_id(sample: Config, capsys: pytest.CaptureFixture[str]) -> None:
    assert _list(capsys, "--parent", "epic") == ["pl-low01"]
    child = _new(capsys, "Another child", "--type", "task", "--parent", "epic")
    issue = read_issue(sample.issue_path(child), sample)
    assert issue.text("parent") == "pl-epic1"


def test_show_prints_file(sample: Config, capsys: pytest.CaptureFixture[str]) -> None:
    write_issue(Issue({"id": "pl-body1", "title": "With body", "type": "bug", "status": "open",
                       "created": "2026-01-01"}, "Café text.\n"), sample)
    assert main(["show", "body1"]) == 0
    assert capsys.readouterr().out == (
        "---\nid: pl-body1\ntitle: With body\ntype: bug\nstatus: open\ncreated: 2026-01-01\n---\n"
        "\nCafé text.\n"
    )


def test_show_json(sample: Config, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["show", "pl-low01", "--json"]) == 0
    assert capsys.readouterr().out == (
        '{"id": "pl-low01", "title": "Low child", "type": "task", "status": "open", '
        '"priority": "low", "parent": "pl-epic1", "labels": ["a", "b"], "created": "2026-01-01", '
        '"body": "", "blocks": []}\n'
    )


def test_show_ignores_unrelated_corrupt_file(sample: Config, capsys: pytest.CaptureFixture[str]) -> None:
    write_raw(sample, "pl-bad01", "junk\n")
    assert main(["show", "high1"]) == 0
    assert "High one" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["zzz"], "no issue matches 'zzz'"),
        (["pl-"], "'pl-' is ambiguous"),
        (["bad01"], "issues/b/pl-bad01.md:1: first line must be '---'"),
        (["stray"], "issues/pl-stray.md: in the wrong bucket; it belongs at issues/s/pl-stray.md"),
    ],
)
def test_show_rejects(sample: Config, capsys: pytest.CaptureFixture[str],
                      args: list[str], message: str) -> None:
    write_raw(sample, "pl-bad01", "junk\n")
    (sample.issues_path / "pl-stray.md").write_text("junk\n", encoding="utf-8")
    assert main(["show", *args]) == 1
    assert message in capsys.readouterr().err


def test_close(sample: Config, capsys: pytest.CaptureFixture[str]) -> None:
    path = sample.issue_path("pl-high1")
    before: str = path.read_text(encoding="utf-8")
    assert main(["close", "high1"]) == 0
    assert capsys.readouterr().out == "closed pl-high1\n"
    assert path.read_text(encoding="utf-8") == before.replace("status: open", "status: closed")
    assert "pl-high1" not in _list(capsys)


def test_close_keeps_hand_edits(sample: Config, capsys: pytest.CaptureFixture[str]) -> None:
    text = "---\ntitle: Hand  made\nid: pl-hand1\nstatus: open\ntype: bug\ncreated: 2026-01-01\n---\nx\n"
    path = write_raw(sample, "pl-hand1", text)
    assert main(["close", "hand1"]) == 0
    capsys.readouterr()
    assert path.read_text(encoding="utf-8") == text.replace("status: open", "status: closed")


def test_close_already_closed(sample: Config, capsys: pytest.CaptureFixture[str]) -> None:
    path = sample.issue_path("pl-done1")
    before: bytes = path.read_bytes()
    assert main(["close", "done1"]) == 0
    assert capsys.readouterr().out == "pl-done1 already closed\n"
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["zzz"], "no issue matches 'zzz'"),
        (["bad01"], "issues/b/pl-bad01.md:1: first line must be '---'"),
    ],
)
def test_close_rejects(sample: Config, capsys: pytest.CaptureFixture[str],
                       args: list[str], message: str) -> None:
    path = write_raw(sample, "pl-bad01", "junk\n")
    assert main(["close", *args]) == 1
    assert message in capsys.readouterr().err
    assert path.read_text(encoding="utf-8") == "junk\n"


def _set(capsys: pytest.CaptureFixture[str], *args: str) -> str:
    """Run `punch set`, assert it succeeded, and return its output."""
    assert main(["set", *args]) == 0
    return capsys.readouterr().out


def test_set_changes_and_adds(sample: Config, capsys: pytest.CaptureFixture[str]) -> None:
    path = sample.issue_path("pl-none1")
    before: str = path.read_text(encoding="utf-8")
    out = _set(capsys, "none1", "priority=high", "labels= x ,y", "title=  Renamed  ")
    assert out == "updated pl-none1: priority, labels, title\n"
    assert path.read_text(encoding="utf-8") == before.replace(
        "title: No priority", "title: Renamed"
    ).replace("status: open\n", "status: open\npriority: high\nlabels: x, y\n")
    assert main(["check"]) == 0


def test_set_removes_optional_field(sample: Config, capsys: pytest.CaptureFixture[str]) -> None:
    assert _set(capsys, "low01", "parent=", "labels=") == (
        "updated pl-low01: parent, labels (removed a, b)\n"
    )
    issue = read_issue(sample.issue_path("pl-low01"), sample)
    assert issue.text("parent") is None
    assert issue.items("labels") == []


def test_set_parent_accepts_short_id(sample: Config, capsys: pytest.CaptureFixture[str]) -> None:
    assert _set(capsys, "high1", "parent=epic") == "updated pl-high1: parent\n"
    assert read_issue(sample.issue_path("pl-high1"), sample).text("parent") == "pl-epic1"


def test_set_reopens(sample: Config, capsys: pytest.CaptureFixture[str]) -> None:
    assert _set(capsys, "done1", "status=open") == "updated pl-done1: status\n"
    assert "pl-done1" in _list(capsys)


def test_set_unchanged_leaves_file_alone(sample: Config, capsys: pytest.CaptureFixture[str]) -> None:
    path = sample.issue_path("pl-low01")
    before: bytes = path.read_bytes()
    assert _set(capsys, "low01", "priority=low", "labels=a,b", "category=") == "pl-low01 unchanged\n"
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["none1", "priority"], "expected field=value, got 'priority'"),
        (["none1", "priority=high", "priority=low"], "priority is set twice"),
        (["none1", "colour=red"], "colour is not a field in the config"),
        (["none1", "id=pl-zzzzz"], "id cannot be changed; it names the file"),
        (["none1", "title="], "title is required and cannot be removed"),
        (["none1", "priority=urgent"], "priority 'urgent' is not one of"),
        (["none1", "category=web"], "category 'web' is not one of"),
        (["none1", "labels=a,,b"], "empty item in labels"),
        (["none1", "created=soon"], "not a YYYY-MM-DD date"),
        (["none1", "title=a\nstatus: closed"], "title must be a single line"),
        (["none1", "parent=zzz"], "no issue matches 'zzz'"),
        (["none1", "parent=none1"], "parent loop: pl-none1 -> pl-none1"),
        (["epic1", "parent=low01"], "parent loop: pl-epic1 -> pl-low01 -> pl-epic1"),
        (["zzz", "priority=high"], "no issue matches 'zzz'"),
    ],
)
def test_set_rejects(sample: Config, capsys: pytest.CaptureFixture[str],
                     args: list[str], message: str) -> None:
    before: dict[Path, bytes] = {p: p.read_bytes() for p in sample.issues_path.glob("*/*.md")}
    assert main(["set", *args]) == 1
    assert message in capsys.readouterr().err
    assert {p: p.read_bytes() for p in sample.issues_path.glob("*/*.md")} == before


def test_set_needs_an_assignment(sample: Config) -> None:
    with pytest.raises(SystemExit):
        main(["set", "none1"])


@pytest.fixture
def searchable(sample: Config) -> Config:
    """`sample`, plus issues whose bodies mention retries."""
    write_issue(Issue({"id": "pl-body1", "title": "Network flakiness", "type": "bug",
                       "status": "open", "priority": "medium", "created": "2026-01-01"},
                      "Add a Retry with backoff.\nSecond line.\n"), sample)
    write_issue(Issue({"id": "pl-body2", "title": "Old retry work", "type": "task",
                       "status": "closed", "created": "2026-01-01"}, "Done.\n"), sample)
    return sample


def _search(capsys: pytest.CaptureFixture[str], *args: str) -> list[str]:
    """Run `punch search`, assert it succeeded, and return the ids printed."""
    assert main(["search", *args]) == 0
    return [line.split(" ", 1)[0] for line in capsys.readouterr().out.splitlines()]


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["retry"], ["pl-body1"]),  # body match, case-insensitive; closed pl-body2 left out
        (["retry", "--all"], ["pl-body1", "pl-body2"]),  # title match on the closed one
        (["Retry", "--case-sensitive"], ["pl-body1"]),
        (["retry", "--case-sensitive"], []),
        (["^second line$"], []),  # no MULTILINE: ^ and $ anchor the whole body
        (["back.?off|dash"], ["pl-high1", "pl-body1"]),  # sorted like list: priority first
        (["one"], ["pl-high1"]),
        (["closed one", "--status", "closed"], ["pl-done1"]),
        (["retry", "--all", "--type", "task"], ["pl-body2"]),
        (["i", "--label", "a", "--label", "b"], ["pl-low01"]),  # pl-high1 has only label a
        (["nothing matches this"], []),
    ],
)
def test_search(searchable: Config, capsys: pytest.CaptureFixture[str],
                args: list[str], expected: list[str]) -> None:
    assert _search(capsys, *args) == expected


def test_search_line_format_and_json(searchable: Config, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["search", "flakiness"]) == 0
    assert capsys.readouterr().out == "pl-body1 open bug medium - Network flakiness\n"
    assert main(["search", "flakiness", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == [
        {"id": "pl-body1", "title": "Network flakiness", "type": "bug", "status": "open",
         "priority": "medium", "created": "2026-01-01"},
    ]


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["(unclosed"], "invalid regex '(unclosed': missing ), unterminated subpattern"),
        (["x", "--type", "tsak"], "type 'tsak' is not one of"),
    ],
)
def test_search_rejects(searchable: Config, capsys: pytest.CaptureFixture[str],
                        args: list[str], message: str) -> None:
    assert main(["search", *args]) == 1
    assert message in capsys.readouterr().err


def test_new_with_rank(in_root: Path, config: Config, capsys: pytest.CaptureFixture[str]) -> None:
    issue_id = _new(capsys, "T", "--type", "bug", "--priority", "high", "--rank", "2.5")
    assert read_issue(config.issue_path(issue_id), config).text("rank") == "2.5"
    assert main(["new", "T", "--type", "bug", "--rank", "soon"]) == 1
    assert "rank 'soon' is not a number like 2 or 2.5" in capsys.readouterr().err


def test_set_rank(sample: Config, capsys: pytest.CaptureFixture[str]) -> None:
    assert _set(capsys, "high1", "rank=3.75") == "updated pl-high1: rank\n"
    assert read_issue(sample.issue_path("pl-high1"), sample).rank == 3.75
    assert main(["set", "high1", "rank=1,2"]) == 1
    assert "rank '1,2' is not a number" in capsys.readouterr().err
    assert _set(capsys, "high1", "rank=") == "updated pl-high1: rank\n"
    assert read_issue(sample.issue_path("pl-high1"), sample).rank is None


def test_sort_priority_then_rank_then_created(in_root: Path, config: Config,
                                              capsys: pytest.CaptureFixture[str]) -> None:
    _make(config, "pl-r0001", "high, unranked, older", priority="high", created="2025-01-01")
    _make(config, "pl-r0002", "high, rank 10", priority="high", rank="10")
    _make(config, "pl-r0003", "high, rank 2", priority="high", rank="2")
    _make(config, "pl-r0004", "high, rank -1.5", priority="high", rank="-1.5")
    _make(config, "pl-r0005", "high, rank 2.25", priority="high", rank="2.25")
    _make(config, "pl-r0006", "low, rank 1", priority="low", rank="1")
    _make(config, "pl-r0007", "no priority, rank 1", rank="1")
    assert _list(capsys) == [
        "pl-r0004", "pl-r0003", "pl-r0005", "pl-r0002", "pl-r0001",  # 10 after 2.25: numeric
        "pl-r0006", "pl-r0007",
    ]
    assert main(["list", "--priority", "low", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["rank"] == "1"


def test_set_rank_needs_the_field(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                  capsys: pytest.CaptureFixture[str]) -> None:
    # A config written before rank existed has no rank field; set says so instead of writing.
    data = default_config("pl")
    data["fields"].remove("rank")
    (tmp_path / CONFIG_NAME).write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    issue_id = _new(capsys, "T", "--type", "bug")
    assert main(["set", issue_id, "rank=1"]) == 1
    assert "rank is not a field in the config" in capsys.readouterr().err


def test_search_fails_closed_on_bad_file(searchable: Config, capsys: pytest.CaptureFixture[str]) -> None:
    write_raw(searchable, "pl-bad01", "junk\n")
    assert main(["search", "retry"]) == 1
    assert "(run `punch check` to find corrupt files)" in capsys.readouterr().err


CONTEXT_BODY = "".join(f"line {n}\n" for n in range(1, 11)).replace("line 3", "needle three").replace(
    "line 8", "needle eight"
)


@pytest.fixture
def long_body(in_root: Path, config: Config) -> Config:
    """pl-ctx01: ten body lines (file lines 9-18), `needle` on body lines 3 and 8."""
    write_issue(Issue({"id": "pl-ctx01", "title": "Context", "type": "task", "status": "open",
                       "created": "2026-01-01"}, CONTEXT_BODY), config)
    return config


def _search_out(capsys: pytest.CaptureFixture[str], *args: str) -> str:
    assert main(["search", *args]) == 0
    return capsys.readouterr().out


def test_context_zero_shows_matching_lines(long_body: Config,
                                           capsys: pytest.CaptureFixture[str]) -> None:
    # Two --- lines and five fields, then the blank separator: body line 1 is file line 9.
    assert _search_out(capsys, "needle", "--context", "0") == (
        "pl-ctx01 open task - - Context\n"
        "  11: needle three\n"
        "  --\n"
        "  16: needle eight\n"
    )


def test_context_lines_either_side(long_body: Config, capsys: pytest.CaptureFixture[str]) -> None:
    assert _search_out(capsys, "needle", "--context", "1") == (
        "pl-ctx01 open task - - Context\n"
        "  10- line 2\n"
        "  11: needle three\n"
        "  12- line 4\n"
        "  --\n"
        "  15- line 7\n"
        "  16: needle eight\n"
        "  17- line 9\n"
    )


def test_context_groups_merge_when_they_touch(long_body: Config,
                                              capsys: pytest.CaptureFixture[str]) -> None:
    out = _search_out(capsys, "needle", "--context", "2")
    assert "--\n" not in out  # 3+2 and 8-2 meet at body line 5/6, so one group
    assert out.splitlines()[1:] == [f"  {n}{':' if n in (11, 16) else '-'} " + text
                                    for n, text in zip(range(9, 19), CONTEXT_BODY.splitlines())]


def test_context_clamps_at_body_edges(long_body: Config, capsys: pytest.CaptureFixture[str]) -> None:
    out = _search_out(capsys, "line 1$|line 10", "--context", "5")
    assert out.splitlines()[1] == "  9: line 1"
    assert out.splitlines()[-1] == "  18: line 10"


def test_context_title_only_match_has_no_lines(long_body: Config,
                                               capsys: pytest.CaptureFixture[str]) -> None:
    assert _search_out(capsys, "context", "--context", "3") == "pl-ctx01 open task - - Context\n"


def test_context_body_line_without_blank_separator(in_root: Path, config: Config,
                                                   capsys: pytest.CaptureFixture[str]) -> None:
    write_raw(config, "pl-hand1",
              "---\nid: pl-hand1\ntitle: T\ntype: bug\nstatus: open\ncreated: 2026-01-01\n---\n"
              "needle on file line 8\n")
    assert _search_out(capsys, "needle", "--context", "0").splitlines()[1] == (
        "  8: needle on file line 8"
    )


def test_context_clips_long_lines(in_root: Path, config: Config,
                                  capsys: pytest.CaptureFixture[str]) -> None:
    line = "a" * 200 + " needle " + "b" * 200
    write_issue(Issue({"id": "pl-long1", "title": "T", "type": "bug", "status": "open",
                       "created": "2026-01-01"}, line + "\n"), config)
    shown = _search_out(capsys, "needle", "--context", "0").splitlines()[1]
    text = shown.split(": ", 1)[1]
    assert text.startswith("...") and text.endswith("...")
    assert " needle " in text
    assert len(text) == 160 + 6


def test_context_json(long_body: Config, capsys: pytest.CaptureFixture[str]) -> None:
    rows = json.loads(_search_out(capsys, "three", "--context", "1", "--json"))
    assert rows[0]["matches"] == [
        {"line": 10, "text": "line 2", "match": False},
        {"line": 11, "text": "needle three", "match": True},
        {"line": 12, "text": "line 4", "match": False},
    ]
    assert "matches" not in json.loads(_search_out(capsys, "three", "--json"))[0]


@pytest.mark.parametrize("value", ["-1", "two", "1.5"])
def test_context_rejects(long_body: Config, capsys: pytest.CaptureFixture[str], value: str) -> None:
    with pytest.raises(SystemExit):
        main(["search", "needle", "--context", value])
    assert "expected a whole number 0 or more" in capsys.readouterr().err
