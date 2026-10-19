"""`punch import-md`: each roadmap shape, failing closed, and the CLI around it."""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from punchlist.cli import main
from punchlist.config import CONFIG_NAME, Config, load_config
from punchlist.importer import ImportPlan, Unclassified, item_title, plan_import, rewrite_links
from punchlist.issue import Issue

FIXTURE: Path = Path(__file__).parent / "fixtures" / "roadmap_shapes.md"
TODAY = "2026-10-19"


def _counter() -> Iterator[str]:
    """Deterministic id characters: ids come out pl-00000, pl-00001, ..."""
    n = 0
    while True:
        for char in f"{n:05d}":
            yield char
        n += 1


def _plan(config: Config, text: str, source: Path | None = None) -> ImportPlan:
    chars = _counter()
    return plan_import(text, source or config.root / "ROADMAP.md", config, TODAY,
                       lambda _alphabet: next(chars))


@pytest.fixture
def plan(config: Config) -> ImportPlan:
    """The shapes fixture, planned as if it sat at the project root."""
    return _plan(config, FIXTURE.read_text(encoding="utf-8"))


def _by_title(plan: ImportPlan) -> dict[str, Issue]:
    return {issue.title: issue for issue in plan.issues}


def test_titles_and_types_in_order(plan: ImportPlan) -> None:
    assert [(i.title, i.text("type")) for i in plan.issues] == [
        ("Bold task", "task"),
        ("Plain task", "task"),
        ("Plain sentence task", "task"),
        ("An epic", "epic"),
        ("Child one", "task"),
        ("Child two", "task"),
        ("Nested epic", "epic"),
        ("Grandchild", "task"),
        ("Task with children", "task"),
        ("Its child", "task"),
        ("Task with a table", "task"),
        ("Second epic", "epic"),
        ("Deep child", "task"),
    ]
    assert plan.notes == 2
    assert plan.unclassified == []


def test_parents(plan: ImportPlan) -> None:
    issues = _by_title(plan)
    parent_titles: dict[str, str] = {}
    by_id = {issue.id: issue.title for issue in plan.issues}
    for title, issue in issues.items():
        parent: str | None = issue.text("parent")
        if parent is not None:
            parent_titles[title] = by_id[parent]
    assert parent_titles == {
        "Child one": "An epic",
        "Child two": "An epic",
        "Nested epic": "An epic",
        "Grandchild": "Nested epic",
        "Its child": "Task with children",
        "Deep child": "Second epic",
    }


def test_categories_and_labels(plan: ImportPlan) -> None:
    issues = _by_title(plan)
    assert issues["Bold task"].text("category") == "core-engine"
    assert issues["Bold task"].items("labels") == ["parsing"]
    assert issues["Grandchild"].items("labels") == ["parsing"]
    assert issues["Its child"].items("labels") == ["layout"]
    assert issues["Task with a table"].text("category") == "web-and-ui"
    assert issues["Task with a table"].items("labels") == []
    assert plan.missing_categories == ["core-engine", "web-and-ui"]


def test_fixed_fields(plan: ImportPlan) -> None:
    issue = plan.issues[0]
    assert issue.id == "pl-00000"
    assert issue.status == "open"
    assert issue.text("created") == TODAY


def test_body_is_full_text_with_links_rewritten(plan: ImportPlan) -> None:
    # Relative to the issue's bucket directory, issues/0/, not to issues/.
    assert _by_title(plan)["Bold task"].body == (
        "**Bold task** (ref 1.2) -- body with a [link](../../docs/x.md#part) and "
        "[web](https://example.com) and [anchor](#here).\n"
    )
    assert _by_title(plan)["Plain sentence task"].body == (
        "Plain sentence task. More words after it.\n"
    )


def test_notes_fold_into_parent(plan: ImportPlan) -> None:
    assert _by_title(plan)["An epic"].body == (
        "**An epic** -- with children:\n"
        "\n"
        "- A note about the epic, with **bold** inside.\n"
        "- **Bold note**: no checkbox, no children.\n"
    )


def test_continuations_follow_indentation(plan: ImportPlan) -> None:
    issues = _by_title(plan)
    assert issues["Task with a table"].body == (
        "**Task with a table** -- see below.\n"
        "\n"
        "| A | B |\n"
        "|---|---|\n"
        "| 1 | 2 |\n"
        "\n"
        "A closing paragraph, still part of the task.\n"
    )
    assert issues["Deep child"].body == (
        "**Deep child** -- with a continuation.\n"
        "\n"
        "Indented four, so it belongs to the deep child.\n"
    )
    assert issues["Second epic"].body == (
        "**Second epic** -- the last one:\n"
        "\n"
        "Indented two, so it belongs to the epic, not the deep child.\n"
    )


def test_categories_already_configured_are_not_missing(config: Config) -> None:
    plan = _plan(config, "## Docs\n\n- [ ] **T**\n")
    assert plan.missing_categories == []


@pytest.mark.parametrize(
    ("text", "unclassified"),
    [
        ("## A\n\nSection prose.\nMore.\n\nAnd more.\n\n- [ ] **T**\n",
         [Unclassified(3, 6, "prose outside any item")]),
        ("## A\n\n- [x] **Done**\n", [Unclassified(3, 3, "checkbox [x] is not [ ]")]),
        ("## A\n\n- A note with no parent.\n", [Unclassified(3, 3, "note with no parent item")]),
        ("## A\n\n- [ ] **Unclosed bold\n", [Unclassified(3, 3, "no title")]),
        ("## A\n\n#### Deep\n", [Unclassified(3, 3, "unexpected heading: #### Deep")]),
        ("### Before any section\n", [Unclassified(1, 1, "unexpected heading: ### Before any section")]),
        ("intro\n# Late title\n", [Unclassified(1, 1, "prose outside any item"),
                                   Unclassified(2, 2, "unexpected heading: # Late title")]),
        ("## A\n\n- [ ] **T**\n\tTabbed.\n", [Unclassified(4, 4, "tab indentation")]),
        ("## A\n\n- [ ] **T**\nLazy continuation at column 0.\n",
         [Unclassified(4, 4, "prose outside any item")]),
    ],
)
def test_unclassified(config: Config, text: str, unclassified: list[Unclassified]) -> None:
    assert _plan(config, text).unclassified == unclassified


@pytest.mark.parametrize(
    ("text", "title"),
    [
        ("**Bold** (CR 1) -- rest", "Bold"),
        ("**Ends in a period.** Rest.", "Ends in a period"),
        ("**Deferred on purpose:** rest", "Deferred on purpose"),
        ("Plain -- rest", "Plain"),
        ("Plain: rest", "Plain"),
        ("Plain; rest", "Plain"),
        ("First (ref 6.8). Second -- third", "First (ref 6.8)"),
        ("Version 1.2 is out -- rest", "Version 1.2 is out"),
        ("No break at all", "No break at all"),
        ("x" * 50 + " " + "y" * 60, "x" * 50 + "..."),
        ("****", None),
        ("**", None),
    ],
)
def test_item_title(text: str, title: str | None) -> None:
    assert item_title(text) == title


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("[a](docs/x.md)", "[a](../docs/x.md)"),
        ("[a](docs/)", "[a](../docs/)"),
        ("[a](docs/x.md#sec)", "[a](../docs/x.md#sec)"),
        ("[a](./README.md)", "[a](../README.md)"),
        ("[a](#sec)", "[a](#sec)"),
        ("[a](https://x.org/y)", "[a](https://x.org/y)"),
        ("[a](mailto:me@x.org)", "[a](mailto:me@x.org)"),
        ("[a](/abs/path)", "[a](/abs/path)"),
    ],
)
def test_rewrite_links(tmp_path: Path, text: str, expected: str) -> None:
    assert rewrite_links(text, tmp_path, tmp_path / "issues") == expected


def test_rewrite_links_from_elsewhere(tmp_path: Path) -> None:
    assert rewrite_links("[a](x.md)", tmp_path / "notes", tmp_path / "issues") == (
        "[a](../notes/x.md)"
    )


@pytest.fixture
def roadmap(root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The shapes fixture copied into the root, which is the working directory."""
    monkeypatch.chdir(root)
    shutil.copy(FIXTURE, root / "ROADMAP.md")
    return root / "ROADMAP.md"


def test_cli_dry_run(roadmap: Path, config: Config, capsys: pytest.CaptureFixture[str]) -> None:
    before: str = (config.root / CONFIG_NAME).read_text(encoding="utf-8")
    assert main(["import-md", "ROADMAP.md", "--dry-run"]) == 1
    assert capsys.readouterr().out == (
        "would create 13 issues (3 epic, 10 task); 2 notes folded into parents\n"
        "  core-engine: 10\n"
        "  web-and-ui: 3\n"
        "categories missing from punchlist.json (add them with --add-categories): "
        "core-engine, web-and-ui\n"
    )
    assert not config.issues_path.exists()
    assert (config.root / CONFIG_NAME).read_text(encoding="utf-8") == before


def test_cli_refuses_missing_categories(roadmap: Path, config: Config,
                                        capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["import-md", "ROADMAP.md"]) == 1
    assert "punch: error: nothing imported" in capsys.readouterr().err
    assert not config.issues_path.exists()


def test_cli_imports_with_add_categories(roadmap: Path, config: Config,
                                         capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["import-md", "ROADMAP.md", "--add-categories"]) == 0
    assert capsys.readouterr().out.endswith("created 13 issues\n")
    assert load_config(config.root).categories == ("tooling", "docs", "core-engine", "web-and-ui")
    assert len(list(config.issues_path.glob("*/*.md"))) == 13
    assert main(["check"]) == 0
    assert capsys.readouterr().out == "13 issues ok\n"
    assert main(["list", "--type", "epic", "--json"]) == 0
    epics = json.loads(capsys.readouterr().out)
    assert sorted(e["title"] for e in epics) == ["An epic", "Nested epic", "Second epic"]


def test_cli_unclassified(roadmap: Path, config: Config, capsys: pytest.CaptureFixture[str]) -> None:
    roadmap.write_text("## Docs\n\nProse.\n\n- [ ] **T**\n", encoding="utf-8")
    assert main(["import-md", "ROADMAP.md", "--dry-run"]) == 1
    assert capsys.readouterr().out == (
        "would create 1 issue (1 task); 0 notes folded into parents\n"
        "  docs: 1\n"
        "unclassified lines (move them, or pass --skip-unclassified):\n"
        "  ROADMAP.md:3: prose outside any item\n"
    )
    assert main(["import-md", "ROADMAP.md"]) == 1
    assert not config.issues_path.exists()
    capsys.readouterr()
    assert main(["import-md", "ROADMAP.md", "--skip-unclassified"]) == 0
    assert len(list(config.issues_path.glob("*/*.md"))) == 1


def test_cli_missing_file(roadmap: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["import-md", "nope.md", "--dry-run"]) == 1
    assert "nope.md: cannot read" in capsys.readouterr().err
