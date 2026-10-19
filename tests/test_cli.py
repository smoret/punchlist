"""The `punch` command line, driven through main()."""

from __future__ import annotations

import datetime
import io
from pathlib import Path

import pytest

from punchlist.cli import main
from punchlist.config import CONFIG_NAME, Config, load_config
from punchlist.issue import FieldValue, Issue, read_issue, write_issue


def test_init_writes_loadable_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                     capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["init", "--prefix", "pl"]) == 0
    assert load_config(tmp_path).prefix == "pl"
    assert capsys.readouterr().out.startswith("wrote ")


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
    path = config.issues_path / f"{issue_id}.md"
    assert path.read_text(encoding="utf-8") == (
        f"---\nid: {issue_id}\ntitle: Fix the thing\ntype: bug\nstatus: open\n"
        f"created: {datetime.date.today().isoformat()}\n---\n"
    )


def test_new_all_fields(in_root: Path, config: Config, capsys: pytest.CaptureFixture[str]) -> None:
    parent = _new(capsys, "Epic", "--type", "epic")
    child = _new(capsys, "  Child  ", "--type", "task", "--priority", "high",
                 "--category", "docs", "--parent", parent,
                 "--label", "a, b", "--label", "c", "--body", "\n\nLine one.\n\n")
    issue = read_issue(config.issues_path / f"{child}.md", config)
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
    assert (config.issues_path / f"{issue_id}.md").is_file()


def test_new_body_file(in_root: Path, config: Config, capsys: pytest.CaptureFixture[str]) -> None:
    (in_root / "body.md").write_text("From a file — UTF-8.\n", encoding="utf-8")
    issue_id = _new(capsys, "T", "--type", "bug", "--body-file", "body.md")
    assert read_issue(config.issues_path / f"{issue_id}.md", config).body == "From a file — UTF-8.\n"


def test_new_body_stdin(in_root: Path, config: Config, monkeypatch: pytest.MonkeyPatch,
                        capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("From stdin.\n"))
    issue_id = _new(capsys, "T", "--type", "bug", "--body-file", "-")
    assert read_issue(config.issues_path / f"{issue_id}.md", config).body == "From stdin.\n"


def test_new_blank_body_is_empty(in_root: Path, config: Config, capsys: pytest.CaptureFixture[str]) -> None:
    issue_id = _new(capsys, "T", "--type", "bug", "--body", "\n  \n")
    assert read_issue(config.issues_path / f"{issue_id}.md", config).body == ""


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["T"], "required: --type"),
        (["T", "--type", "nope"], "type 'nope' is not one of"),
        (["T", "--type", "bug", "--priority", "urgent"], "priority 'urgent'"),
        (["T", "--type", "bug", "--category", "web"], "category 'web'"),
        (["T", "--type", "bug", "--parent", "pl-zzzzz"], "parent 'pl-zzzzz' does not exist"),
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
        (["--parent", "pl-zzzzz"], "parent 'pl-zzzzz' does not exist"),
    ],
)
def test_list_rejects(sample: Config, capsys: pytest.CaptureFixture[str],
                      args: list[str], message: str) -> None:
    assert main(["list", *args]) == 1
    assert message in capsys.readouterr().err


def test_list_fails_closed_on_bad_file(sample: Config, capsys: pytest.CaptureFixture[str]) -> None:
    (sample.issues_path / "pl-bad01.md").write_text("---\nid: pl-bad01\n", encoding="utf-8")
    assert main(["list"]) == 1
    err: str = capsys.readouterr().err
    assert "pl-bad01.md:1: header has no closing" in err
    assert err.rstrip().endswith("(run `punch check` to find corrupt files)")
