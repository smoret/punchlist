"""Links: `depends-on`, its derived reverse, --ready/--blocked, and the checks that guard it."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from helpers import write_raw

from punchlist.check import check_issues, dependency_loop
from punchlist.cli import main
from punchlist.config import CONFIG_NAME, Config, default_config, parse_config
from punchlist.errors import PunchError
from punchlist.issue import FieldValue, Issue, read_issue, write_issue


def _make(config: Config, issue_id: str, *, status: str = "open",
          depends: list[str] | None = None) -> None:
    fields: dict[str, FieldValue] = {"id": issue_id, "title": issue_id, "type": "task",
                                     "status": status, "created": "2026-01-01"}
    if depends:
        fields["depends-on"] = depends
    write_issue(Issue(fields), config)


@pytest.fixture
def chain(config: Config, monkeypatch: pytest.MonkeyPatch) -> Config:
    """pl-ddddd waits on pl-ccccc (open) and pl-aaaaa (closed); pl-eeeee waits on pl-aaaaa."""
    monkeypatch.chdir(config.root)
    _make(config, "pl-aaaaa", status="closed")
    _make(config, "pl-ccccc")
    _make(config, "pl-ddddd", depends=["pl-ccccc", "pl-aaaaa"])
    _make(config, "pl-eeeee", depends=["pl-aaaaa"])
    return config


def _ids(capsys: pytest.CaptureFixture[str], *args: str) -> list[str]:
    assert main(list(args)) == 0
    return [line.split(" ", 1)[0] for line in capsys.readouterr().out.splitlines()]


def test_config_default_has_links() -> None:
    data = default_config("pl")
    assert "depends-on" in data["fields"] and "depends-on" in data["list_fields"]


def test_config_depends_on_must_be_a_list_field() -> None:
    data: dict[str, Any] = default_config("pl")
    data["list_fields"] = ["labels"]
    with pytest.raises(PunchError, match="'depends-on' must also be in list_fields"):
        parse_config(data, Path("."), Path(CONFIG_NAME))


def test_ready_and_blocked(chain: Config, capsys: pytest.CaptureFixture[str]) -> None:
    # pl-ddddd waits on open pl-ccccc; pl-eeeee's only dependency is closed.
    assert _ids(capsys, "list", "--blocked") == ["pl-ddddd"]
    assert _ids(capsys, "list", "--ready") == ["pl-ccccc", "pl-eeeee"]
    assert _ids(capsys, "search", "pl-", "--ready") == ["pl-ccccc", "pl-eeeee"]
    assert main(["close", "ccccc"]) == 0
    capsys.readouterr()
    assert _ids(capsys, "list", "--ready") == ["pl-ddddd", "pl-eeeee"]


def test_search_ready_sees_dependencies_outside_the_hits(chain: Config,
                                                         capsys: pytest.CaptureFixture[str]) -> None:
    # pl-ccccc doesn't match "ddddd", but its status still decides whether pl-ddddd is ready.
    assert _ids(capsys, "search", "ddddd", "--blocked") == ["pl-ddddd"]
    assert _ids(capsys, "search", "ddddd", "--ready") == []


def test_missing_dependency_blocks(chain: Config, capsys: pytest.CaptureFixture[str]) -> None:
    write_raw(chain, "pl-fffff", "---\nid: pl-fffff\ntitle: T\ntype: task\nstatus: open\n"
                                 "depends-on: pl-zzzzz\ncreated: 2026-01-01\n---\n")
    assert "pl-fffff" in _ids(capsys, "list", "--blocked")


def test_show_json_blocks_is_derived(chain: Config, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["show", "aaaaa", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["blocks"] == ["pl-ddddd", "pl-eeeee"]
    assert main(["show", "ddddd", "--json"]) == 0
    row = json.loads(capsys.readouterr().out)
    assert row["depends-on"] == ["pl-ccccc", "pl-aaaaa"]
    assert row["blocks"] == []


def test_new_depends_on(chain: Config, capsys: pytest.CaptureFixture[str]) -> None:
    [new_id] = _ids(capsys, "new", "T", "--type", "task", "--depends-on", "cccc,aaaa",
                    "--depends-on", "ccccc")
    issue = read_issue(chain.issue_path(new_id), chain)
    assert issue.items("depends-on") == ["pl-ccccc", "pl-aaaaa"]  # resolved, repeats dropped


def test_set_depends_on(chain: Config, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["set", "eeeee", "depends-on=ccc, ddd"]) == 0
    assert capsys.readouterr().out == "updated pl-eeeee: depends-on\n"
    assert read_issue(chain.issue_path("pl-eeeee"), chain).items("depends-on") == [
        "pl-ccccc", "pl-ddddd"
    ]
    assert main(["set", "eeeee", "depends-on="]) == 0
    assert read_issue(chain.issue_path("pl-eeeee"), chain).items("depends-on") == []


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["set", "ccccc", "depends-on=ccccc"], "dependency loop: pl-ccccc -> pl-ccccc"),
        (["set", "ccccc", "depends-on=ddddd"], "dependency loop: pl-ccccc -> pl-ddddd -> pl-ccccc"),
        (["set", "aaaaa", "depends-on=eeeee"], "dependency loop: pl-aaaaa -> pl-eeeee -> pl-aaaaa"),
        (["set", "ccccc", "depends-on=zzz"], "no issue matches 'zzz'"),
        (["set", "ccccc", "depends-on=a,,d"], "empty item in depends-on"),
        (["new", "T", "--type", "task", "--depends-on", "zzz"], "no issue matches 'zzz'"),
    ],
)
def test_links_rejected(chain: Config, capsys: pytest.CaptureFixture[str],
                        args: list[str], message: str) -> None:
    before: dict[Path, bytes] = {p: p.read_bytes() for p in chain.issues_path.glob("*/*.md")}
    assert main(args) == 1
    assert message in capsys.readouterr().err
    assert {p: p.read_bytes() for p in chain.issues_path.glob("*/*.md")} == before


def test_dependency_loop_finds_a_shortest_loop(chain: Config) -> None:
    _make(chain, "pl-fffff", depends=["pl-ddddd"])
    # pl-ccccc -> pl-fffff -> pl-ddddd -> pl-ccccc, found by walking down from pl-fffff.
    assert dependency_loop(chain, "pl-ccccc", ["pl-fffff"]) == [
        "pl-ccccc", "pl-fffff", "pl-ddddd", "pl-ccccc"
    ]
    assert dependency_loop(chain, "pl-ccccc", ["pl-eeeee"]) is None


def _check(config: Config) -> list[str]:
    return [p.describe(config.root) for p in check_issues(config)[1]]


HEADER = "---\nid: {id}\ntitle: T\ntype: task\nstatus: open\ndepends-on: {deps}\ncreated: 2026-01-01\n---\n"


def _raw(config: Config, issue_id: str, deps: str) -> None:
    write_raw(config, issue_id, HEADER.format(id=issue_id, deps=deps))


def test_check_missing_dependency(config: Config) -> None:
    _make(config, "pl-aaaaa")
    _raw(config, "pl-bbbbb", "pl-aaaaa, pl-zzzzz")
    assert _check(config) == ["issues/b/pl-bbbbb.md:6: depends-on 'pl-zzzzz' does not exist"]


def test_check_dependency_loops_reported_once_each(config: Config) -> None:
    _raw(config, "pl-ccccc", "pl-aaaaa")
    _raw(config, "pl-aaaaa", "pl-bbbbb")
    _raw(config, "pl-bbbbb", "pl-ccccc, pl-ddddd")  # also branches out of the loop
    _make(config, "pl-ddddd")
    _raw(config, "pl-eeeee", "pl-eeeee")
    _raw(config, "pl-fffff", "pl-aaaaa")  # leads into the loop; not part of it
    assert _check(config) == [
        "issues/a/pl-aaaaa.md:6: dependency loop: pl-aaaaa -> pl-bbbbb -> pl-ccccc -> pl-aaaaa",
        "issues/e/pl-eeeee.md:6: dependency loop: pl-eeeee -> pl-eeeee",
    ]


def test_check_two_loops_sharing_an_issue_is_one_group(config: Config) -> None:
    # aaaaa <-> bbbbb and aaaaa <-> ccccc: one strongly connected group, reported once.
    _raw(config, "pl-aaaaa", "pl-bbbbb, pl-ccccc")
    _raw(config, "pl-bbbbb", "pl-aaaaa")
    _raw(config, "pl-ccccc", "pl-aaaaa")
    assert _check(config) == [
        "issues/a/pl-aaaaa.md:6: dependency loop: pl-aaaaa -> pl-bbbbb -> pl-aaaaa",
    ]


def test_tracker_without_links(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                               capsys: pytest.CaptureFixture[str]) -> None:
    data: dict[str, Any] = default_config("pl")
    for link in ("depends-on", "related"):  # a tracker made before links existed
        data["fields"].remove(link)
        data["list_fields"].remove(link)
    (tmp_path / CONFIG_NAME).write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert main(["new", "T", "--type", "task"]) == 0
    capsys.readouterr()
    assert main(["list", "--ready"]) == 1
    assert "--ready and --blocked need a depends-on field in the config" in capsys.readouterr().err
    assert main(["new", "T", "--type", "task", "--depends-on", "x"]) == 1
    assert "depends-on is not a field in the config" in capsys.readouterr().err
    assert main(["new", "T", "--type", "task", "--related", "x"]) == 1
    assert "related is not a field in the config" in capsys.readouterr().err


@pytest.fixture
def trio(config: Config, monkeypatch: pytest.MonkeyPatch) -> Config:
    """pl-aaaaa, pl-bbbbb, pl-ccccc, unlinked; the working directory is the root."""
    monkeypatch.chdir(config.root)
    for issue_id in ("pl-aaaaa", "pl-bbbbb", "pl-ccccc"):
        _make(config, issue_id)
    return config


def _related(config: Config, issue_id: str) -> list[str]:
    return read_issue(config.issue_path(issue_id), config).items("related")


def _all_bytes(config: Config) -> dict[Path, bytes]:
    return {p: p.read_bytes() for p in config.issues_path.glob("*/*.md")}


def test_set_related_writes_both_sides(trio: Config, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["set", "aaaaa", "related=bbbbb, ccc"]) == 0
    assert capsys.readouterr().out == (
        "updated pl-aaaaa: related\nupdated pl-bbbbb: related\nupdated pl-ccccc: related\n"
    )
    assert _related(trio, "pl-aaaaa") == ["pl-bbbbb", "pl-ccccc"]
    assert _related(trio, "pl-bbbbb") == ["pl-aaaaa"]
    assert _related(trio, "pl-ccccc") == ["pl-aaaaa"]
    assert not _check(trio)


def test_set_related_removes_both_sides(trio: Config, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["set", "aaaaa", "related=bbbbb,ccccc"]) == 0
    assert main(["set", "ccccc", "related=aaaaa,bbbbb"]) == 0  # adds c<->b; a is already there
    capsys.readouterr()
    assert main(["set", "aaaaa", "related=ccccc"]) == 0  # drops a<->b only
    assert capsys.readouterr().out == "updated pl-aaaaa: related\nupdated pl-bbbbb: related\n"
    assert _related(trio, "pl-bbbbb") == ["pl-ccccc"]
    assert main(["set", "ccccc", "related="]) == 0
    assert _related(trio, "pl-ccccc") == []
    assert _related(trio, "pl-aaaaa") == []
    assert _related(trio, "pl-bbbbb") == []
    assert "related:" not in trio.issue_path("pl-bbbbb").read_text(encoding="utf-8")
    assert not _check(trio)


def test_new_related_links_back(trio: Config, capsys: pytest.CaptureFixture[str]) -> None:
    [new_id] = _ids(capsys, "new", "Follow-up", "--type", "task", "--related", "aaaaa",
                    "--related", "bbbbb,aaaaa")
    assert _related(trio, new_id) == ["pl-aaaaa", "pl-bbbbb"]
    assert _related(trio, "pl-aaaaa") == [new_id]
    assert _related(trio, "pl-bbbbb") == [new_id]
    assert not _check(trio)


def test_closing_keeps_related(trio: Config, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["set", "aaaaa", "related=bbbbb"]) == 0
    assert main(["close", "aaaaa"]) == 0
    capsys.readouterr()
    assert _related(trio, "pl-aaaaa") == ["pl-bbbbb"]
    assert main(["show", "bbbbb", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["related"] == ["pl-aaaaa"]


def test_related_never_blocks(trio: Config, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["set", "aaaaa", "related=bbbbb"]) == 0
    capsys.readouterr()
    assert _ids(capsys, "list", "--blocked") == []


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["set", "aaaaa", "related=aaaaa"], "pl-aaaaa can't be related to itself"),
        (["set", "aaaaa", "related=bbbbb,zzz"], "no issue matches 'zzz'"),
        (["set", "aaaaa", "related=bbbbb,,c"], "empty item in related"),
        (["set", "aaaaa", "related=bbbbb", "related=ccccc"], "related is set twice"),
        (["new", "T", "--type", "task", "--related", "zzz"], "no issue matches 'zzz'"),
    ],
)
def test_related_rejected_writes_nothing(trio: Config, capsys: pytest.CaptureFixture[str],
                                         args: list[str], message: str) -> None:
    before = _all_bytes(trio)
    assert main(args) == 1
    assert message in capsys.readouterr().err
    assert _all_bytes(trio) == before


def test_corrupt_partner_stops_the_whole_change(trio: Config,
                                                capsys: pytest.CaptureFixture[str]) -> None:
    write_raw(trio, "pl-ddddd", "---\nid: pl-ddddd\ntitle: T\ntype: task\nstatus: open\n"
                                "priority: urgent\ncreated: 2026-01-01\n---\n")
    before = _all_bytes(trio)
    assert main(["set", "aaaaa", "related=bbbbb,ddddd"]) == 1
    assert "pl-ddddd.md:6: priority 'urgent'" in capsys.readouterr().err
    assert _all_bytes(trio) == before  # not even pl-aaaaa or pl-bbbbb


RELATED_HEADER = "---\nid: {id}\ntitle: T\ntype: task\nstatus: open\nrelated: {related}\ncreated: 2026-01-01\n---\n"


def test_check_related(config: Config) -> None:
    write_raw(config, "pl-aaaaa", RELATED_HEADER.format(id="pl-aaaaa", related="pl-bbbbb"))
    _make(config, "pl-bbbbb")  # doesn't list pl-aaaaa back
    write_raw(config, "pl-ccccc", RELATED_HEADER.format(id="pl-ccccc", related="pl-ccccc"))
    write_raw(config, "pl-ddddd", RELATED_HEADER.format(id="pl-ddddd", related="pl-zzzzz"))
    write_raw(config, "pl-eeeee", RELATED_HEADER.format(id="pl-eeeee", related="pl-fffff"))
    write_raw(config, "pl-fffff", "junk\n")  # its own problem; no one-sided report against it
    assert _check(config) == [
        "issues/a/pl-aaaaa.md:6: related 'pl-bbbbb' does not list 'pl-aaaaa' back",
        "issues/c/pl-ccccc.md:6: related lists the issue itself",
        "issues/d/pl-ddddd.md:6: related 'pl-zzzzz' does not exist",
        "issues/f/pl-fffff.md:1: first line must be '---'",
    ]


def test_config_related_must_be_a_list_field() -> None:
    data: dict[str, Any] = default_config("pl")
    data["list_fields"] = ["depends-on", "labels"]
    with pytest.raises(PunchError, match="'related' must also be in list_fields"):
        parse_config(data, Path("."), Path(CONFIG_NAME))
