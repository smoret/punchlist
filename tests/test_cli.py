"""The `punch` command line, driven through main()."""

from __future__ import annotations

import datetime
import io
from pathlib import Path

import pytest

from punchlist.cli import main
from punchlist.config import CONFIG_NAME, Config, load_config
from punchlist.issue import read_issue


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
