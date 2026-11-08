"""`punch serve`: the board's API, card placement, and the HTTP wrapper."""

from __future__ import annotations

import http.client
import json
import threading
from collections.abc import Iterator
from http import HTTPStatus
from pathlib import Path
from typing import Any

import pytest

from punchlist import ops
from punchlist.cli import main
from punchlist.comment import load_comments
from punchlist.config import CONFIG_NAME, Config, default_config, load_config
from punchlist.errors import PunchError
from punchlist.issue import FieldValue, Issue, read_issue, write_issue
from punchlist.ops import Conflict
from punchlist.web import Board, BoardServer, HttpError, handle, make_server, place, version


@pytest.fixture
def board_config(tmp_path: Path) -> Config:
    """A tracker with a workflow status between open and closed, and a `waiting-on` field."""
    data = default_config("pl")
    data["statuses"] = ["open", "progress", "closed"]
    data["categories"] = ["pipeline"]
    data["fields"].append("waiting-on")
    (tmp_path / CONFIG_NAME).write_text(json.dumps(data), encoding="utf-8")
    return load_config(tmp_path)


@pytest.fixture
def board(board_config: Config) -> Board:
    return Board(board_config.root, "alex", threading.Lock())


def _make(config: Config, issue_id: str, **fields: FieldValue) -> Issue:
    base: dict[str, FieldValue] = {"id": issue_id, "title": issue_id, "type": "task",
                                   "status": "open", "created": "2026-01-01"}
    issue = Issue({**base, **fields})
    write_issue(issue, config)
    return issue


def _issue(config: Config, issue_id: str) -> Issue:
    return read_issue(config.issue_path(issue_id), config)


def _call(board: Board, method: str, path: str, body: Any = None) -> Any:
    status, reply = handle(board, method, path, body)
    assert status in (HTTPStatus.OK, HTTPStatus.CREATED)
    return reply


# --- reading -------------------------------------------------------------------------------

def test_board_lists_issues_with_card_details(board: Board, board_config: Config) -> None:
    _make(board_config, "pl-aaaaa", priority="low")
    _make(board_config, "pl-bbbbb", priority="high", status="progress",
          **{"depends-on": ["pl-aaaaa", "pl-ccccc"]})
    _make(board_config, "pl-ccccc", status="closed")
    _call(board, "POST", "/api/issues/aaaaa/comments", {"body": "hi"})
    reply = _call(board, "GET", "/api/board")
    assert reply["config"]["statuses"] == ["open", "progress", "closed"]
    assert reply["author"] == "alex"
    assert [i["id"] for i in reply["issues"]] == ["pl-bbbbb", "pl-aaaaa", "pl-ccccc"]
    bbbbb = reply["issues"][0]
    assert (bbbbb["blocked"], bbbbb["waiting"]) == (True, ["pl-aaaaa"])
    assert reply["issues"][1]["comments"] == 1
    assert reply["version"] == _call(board, "GET", "/api/version")["version"]


def test_board_has_times_and_time_orders(board: Board, board_config: Config) -> None:
    _make(board_config, "pl-aaaaa", priority="high")
    _make(board_config, "pl-bbbbb", priority="low", created="2026-02-01")
    reply = _call(board, "GET", "/api/board")
    assert reply["times_exact"] is False  # not a git repository: file times
    assert [i["id"] for i in reply["issues"]] == ["pl-aaaaa", "pl-bbbbb"]  # rank order
    assert reply["orders"]["created"] == ["pl-bbbbb", "pl-aaaaa"]
    assert set(reply["orders"]) == {"created", "updated"}
    assert all(i["created_at"].endswith("Z") and i["updated_at"].endswith("Z")
               for i in reply["issues"])


def test_issue_detail_has_blocks_and_comments(board: Board, board_config: Config) -> None:
    _make(board_config, "pl-aaaaa")
    _make(board_config, "pl-bbbbb", **{"depends-on": ["pl-aaaaa"]})
    _call(board, "POST", "/api/issues/pl-aaaaa/comments", {"body": "\nfirst\n\n"})
    reply = _call(board, "GET", "/api/issues/aaa")
    assert reply["blocks"] == ["pl-bbbbb"]
    assert [(c["author"], c["body"]) for c in reply["comments"]] == [("alex", "first\n")]


@pytest.mark.parametrize(("method", "path"), [
    ("GET", "/elsewhere"), ("GET", "/api/nothing"), ("DELETE", "/api/issues/aaaaa"),
])
def test_unknown_routes(board: Board, board_config: Config, method: str, path: str) -> None:
    _make(board_config, "pl-aaaaa")
    with pytest.raises(HttpError) as raised:
        handle(board, method, path, None)
    assert raised.value.status == HTTPStatus.NOT_FOUND


def test_unknown_issue_is_a_punch_error(board: Board) -> None:
    with pytest.raises(PunchError, match="no issue matches"):
        handle(board, "GET", "/api/issues/zzzzz", None)


# --- writing -------------------------------------------------------------------------------

def test_create_in_a_lane(board: Board, board_config: Config) -> None:
    reply = _call(board, "POST", "/api/issues",
                  {"title": " Split assets ", "type": "task", "status": "progress",
                   "category": "pipeline", "labels": ["art"], "body": "Why.\r\n"})
    issue = _issue(board_config, reply["id"])
    assert (issue.title, issue.status, issue.text("category"), issue.items("labels"),
            issue.body) == ("Split assets", "progress", "pipeline", ["art"], "Why.\n")


@pytest.mark.parametrize(("body", "error"), [
    ({"title": "T", "type": "task", "id": "pl-xxxxx"}, "unknown keys: id"),
    ({"title": "T", "type": "chore"}, "type 'chore' is not one of"),
    ({"title": "T", "type": "task", "status": "done"}, "status 'done' is not one of"),
    ({"title": "  ", "type": "task"}, "title must not be empty"),
    ({"title": "T\nstatus: closed", "type": "task"}, "title must be a single line"),
    ({"type": "task"}, "title must not be empty"),
    ({"title": 5, "type": "task"}, "title must be text"),
    (["T"], "expected a JSON object"),
])
def test_create_refuses(board: Board, board_config: Config, body: Any, error: str) -> None:
    with pytest.raises((PunchError, HttpError), match=error):
        handle(board, "POST", "/api/issues", body)
    assert not list(board_config.issues_path.rglob("*.md"))


def test_patch_sets_fields_like_punch_set(board: Board, board_config: Config) -> None:
    _make(board_config, "pl-aaaaa")
    _make(board_config, "pl-bbbbb")
    _call(board, "PATCH", "/api/issues/aaaaa",
          {"set": {"priority": "high", "waiting-on": "the lead's call", "related": ["bbbbb"],
                   "labels": ["a", "b"]}})
    issue = _issue(board_config, "pl-aaaaa")
    assert (issue.text("priority"), issue.text("waiting-on"), issue.items("labels")) == (
        "high", "the lead's call", ["a", "b"])
    assert _issue(board_config, "pl-bbbbb").items("related") == ["pl-aaaaa"]  # both sides
    _call(board, "PATCH", "/api/issues/aaaaa", {"set": {"priority": None}})
    assert _issue(board_config, "pl-aaaaa").text("priority") is None


def test_patch_refuses_a_stale_value(board: Board, board_config: Config) -> None:
    _make(board_config, "pl-aaaaa", priority="low")
    with pytest.raises(Conflict, match="priority is now low, not high"):
        handle(board, "PATCH", "/api/issues/aaaaa",
               {"set": {"priority": "medium"}, "expect": {"priority": "high"}})
    assert _issue(board_config, "pl-aaaaa").text("priority") == "low"
    _call(board, "PATCH", "/api/issues/aaaaa",
          {"set": {"priority": "medium"}, "expect": {"priority": "low"}})
    assert _issue(board_config, "pl-aaaaa").text("priority") == "medium"


@pytest.mark.parametrize(("body", "error"), [
    ({"set": {"status": "done"}}, "status 'done' is not one of"),
    ({"set": {"id": "pl-zzzzz"}}, "id cannot be changed"),
    ({"set": {"title": None}}, "title is required"),
    ({"set": {}}, "set must be an object"),
    ({"set": {"rank": 2}}, "rank must be text"),
])
def test_patch_refuses(board: Board, board_config: Config, body: Any, error: str) -> None:
    _make(board_config, "pl-aaaaa")
    with pytest.raises((PunchError, HttpError), match=error):
        handle(board, "PATCH", "/api/issues/aaaaa", body)


def test_patch_body(board: Board, board_config: Config) -> None:
    _make(board_config, "pl-aaaaa", priority="low")
    before: str = board_config.issue_path("pl-aaaaa").read_text(encoding="utf-8")
    _call(board, "PATCH", "/api/issues/aaaaa",
          {"body": "\r\n\r\nSplit by biome.\r\n\r\nThen by LOD.\r\n\r\n", "expect_body": ""})
    after: str = board_config.issue_path("pl-aaaaa").read_text(encoding="utf-8")
    assert after == before + "\nSplit by biome.\n\nThen by LOD.\n"
    assert _issue(board_config, "pl-aaaaa").text("priority") == "low"
    _call(board, "PATCH", "/api/issues/aaaaa", {"body": "  \n"})  # blank: no body at all
    assert board_config.issue_path("pl-aaaaa").read_text(encoding="utf-8") == before


def test_patch_body_refuses_a_stale_body(board: Board, board_config: Config) -> None:
    _make(board_config, "pl-aaaaa")
    _call(board, "PATCH", "/api/issues/aaaaa", {"body": "An agent's notes.\n"})
    with pytest.raises(Conflict, match="body has changed"):
        handle(board, "PATCH", "/api/issues/aaaaa", {"body": "Mine.", "expect_body": ""})
    assert _issue(board_config, "pl-aaaaa").body == "An agent's notes.\n"


@pytest.mark.parametrize(("body", "error"), [
    ({"body": "x", "set": {"priority": "low"}}, "send either set or body"),
    ({}, "send either set or body"),
    ({"body": None}, "body must be text"),
    ({"body": "x", "expect_body": 3}, "expect_body must be text"),
])
def test_patch_body_refuses(board: Board, board_config: Config, body: Any, error: str) -> None:
    _make(board_config, "pl-aaaaa")
    with pytest.raises(HttpError, match=error):
        handle(board, "PATCH", "/api/issues/aaaaa", body)


def test_comment_refuses_empty(board: Board, board_config: Config) -> None:
    _make(board_config, "pl-aaaaa")
    with pytest.raises(PunchError, match="comment is empty"):
        handle(board, "POST", "/api/issues/aaaaa/comments", {"body": " \n"})
    assert not load_comments(board_config, "pl-aaaaa")


def test_version_follows_any_edit(board: Board, board_config: Config) -> None:
    _make(board_config, "pl-aaaaa")
    before: str = version(board_config)
    assert version(board_config) == before
    _make(board_config, "pl-aaaaa", title="Edited elsewhere, longer")
    assert version(board_config) != before


# --- moving cards --------------------------------------------------------------------------

def test_move_to_another_lane(board: Board, board_config: Config) -> None:
    _make(board_config, "pl-aaaaa")
    _make(board_config, "pl-bbbbb", status="progress", rank="2")
    _call(board, "POST", "/api/issues/aaaaa/move",
          {"status": "progress", "expect_status": "open", "before": "pl-bbbbb"})
    moved = _issue(board_config, "pl-aaaaa")
    assert (moved.status, moved.text("rank")) == ("progress", "1")


def test_move_keeping_rank_changes_only_the_status(board: Board, board_config: Config) -> None:
    _make(board_config, "pl-aaaaa", rank="5")
    _make(board_config, "pl-bbbbb", status="progress", rank="2")
    _call(board, "POST", "/api/issues/aaaaa/move",
          {"status": "progress", "expect_status": "open", "before": "pl-bbbbb",
           "keep_rank": True})
    moved = _issue(board_config, "pl-aaaaa")
    assert (moved.status, moved.text("rank")) == ("progress", "5")
    with pytest.raises(HttpError, match="keep_rank must be true or false"):
        handle(board, "POST", "/api/issues/aaaaa/move",
               {"status": "open", "expect_status": "progress", "before": None, "keep_rank": 1})


def test_move_refuses_a_card_someone_else_moved(board: Board, board_config: Config) -> None:
    _make(board_config, "pl-aaaaa", status="progress")
    with pytest.raises(Conflict, match="status is now progress, not open"):
        handle(board, "POST", "/api/issues/aaaaa/move",
               {"status": "closed", "expect_status": "open", "before": None})
    assert _issue(board_config, "pl-aaaaa").status == "progress"


def test_move_refuses_an_unknown_status(board: Board, board_config: Config) -> None:
    _make(board_config, "pl-aaaaa")
    with pytest.raises(PunchError, match="status 'done' is not one of"):
        handle(board, "POST", "/api/issues/aaaaa/move",
               {"status": "done", "expect_status": "open", "before": None})


def test_move_renumbers_when_nothing_fits(board: Board, board_config: Config) -> None:
    for issue_id in ("pl-aaaaa", "pl-bbbbb", "pl-ccccc"):
        _make(board_config, issue_id)  # unranked: ordered by created, then id
    _call(board, "POST", "/api/issues/aaaaa/move",
          {"status": "open", "expect_status": "open", "before": "pl-ccccc"})
    ranks = {i: _issue(board_config, i).text("rank") for i in ("pl-aaaaa", "pl-bbbbb", "pl-ccccc")}
    assert ranks == {"pl-bbbbb": "1", "pl-aaaaa": "2", "pl-ccccc": "3"}


def _place(config: Config, issues: list[Issue], moved: str, status: str,
           before: str | None) -> dict[str, str]:
    found: Issue = next(i for i in issues if i.id == moved)
    return place(config, issues, found, status, before)


def _issues(*specs: tuple[str, str | None, str | None]) -> list[Issue]:
    """(id, priority, rank) -> open issues created the same day."""
    issues: list[Issue] = []
    for issue_id, priority, rank in specs:
        fields: dict[str, FieldValue] = {"id": issue_id, "title": "T", "type": "task",
                                         "status": "open", "created": "2026-01-01"}
        if priority:
            fields["priority"] = priority
        if rank:
            fields["rank"] = rank
        issues.append(Issue(fields))
    return issues


@pytest.mark.parametrize(("moved", "before", "expected"), [
    ("pl-d", "pl-b", {"pl-d": "1.5"}),      # between two ranks: the midpoint
    ("pl-d", "pl-a", {"pl-d": "0"}),        # to the top: one less than the first
    ("pl-a", "pl-d", {"pl-a": "4"}),        # above an unranked card: one more than the last rank
    ("pl-a", None, {"pl-b": "1", "pl-c": "2", "pl-d": "3", "pl-a": "4"}),  # below it: renumber
    ("pl-b", "pl-c", {}),                   # where it already is: nothing changes
    ("pl-b", "pl-b", {}),                   # dropped on itself
])
def test_place(board_config: Config, moved: str, before: str | None,
               expected: dict[str, str]) -> None:
    issues = _issues(("pl-a", None, "1"), ("pl-b", None, "2"), ("pl-c", None, "3"),
                     ("pl-d", None, None))
    assert _place(board_config, issues, moved, "open", before) == expected


def test_place_halves_without_float_noise(board_config: Config) -> None:
    issues = _issues(("pl-a", None, "0.1"), ("pl-b", None, "0.2"), ("pl-c", None, None))
    assert _place(board_config, issues, "pl-c", "open", "pl-b") == {"pl-c": "0.15"}


def test_place_stays_within_its_priority(board_config: Config) -> None:
    issues = _issues(("pl-h1", "high", "1"), ("pl-h2", "high", "2"), ("pl-l1", "low", "1"),
                     ("pl-l2", "low", "2"), ("pl-l3", "low", "3"))
    # Dropped among the high cards, a low card lands first among the lows.
    assert _place(board_config, issues, "pl-l3", "open", "pl-h2") == {"pl-l3": "0"}


def test_place_renumbers_equal_neighbors(board_config: Config) -> None:
    issues = _issues(("pl-a", None, "1"), ("pl-b", None, "1"), ("pl-c", None, "5"))
    assert _place(board_config, issues, "pl-c", "open", "pl-b") == {
        "pl-a": "1", "pl-c": "2", "pl-b": "3"}


# --- HTTP ----------------------------------------------------------------------------------

@pytest.fixture
def server(board_config: Config) -> Iterator[BoardServer]:
    running: BoardServer = make_server(board_config.root, "alex")
    thread = threading.Thread(target=running.serve_forever, daemon=True)
    thread.start()
    yield running
    running.shutdown()
    running.server_close()


def _request(server: BoardServer, method: str, path: str, body: bytes | None = None,
             headers: dict[str, str] | None = None) -> tuple[int, bytes, str]:
    host, port = server.server_address[0], server.server_address[1]
    connection = http.client.HTTPConnection(str(host), port, timeout=10)
    try:
        connection.request(method, path, body, headers or {})
        response = connection.getresponse()
        return response.status, response.read(), response.getheader("Content-Type") or ""
    finally:
        connection.close()


def test_http_serves_the_page_and_api(server: BoardServer, board_config: Config) -> None:
    _make(board_config, "pl-aaaaa")
    status, page, kind = _request(server, "GET", "/")
    assert (status, kind) == (200, "text/html; charset=utf-8")
    assert b"/api/board" in page
    status, raw, _ = _request(server, "GET", "/api/board")
    assert status == 200 and json.loads(raw)["issues"][0]["id"] == "pl-aaaaa"
    status, raw, _ = _request(server, "POST", "/api/issues/aaaaa/move",
                              json.dumps({"status": "closed", "expect_status": "open",
                                          "before": None}).encode(),
                              {"Content-Type": "application/json"})
    assert status == 200 and _issue(board_config, "pl-aaaaa").status == "closed"


def test_http_errors_are_json(server: BoardServer, board_config: Config) -> None:
    _make(board_config, "pl-aaaaa", status="closed")
    status, raw, _ = _request(server, "POST", "/api/issues/aaaaa/move",
                              json.dumps({"status": "open", "expect_status": "open",
                                          "before": None}).encode(),
                              {"Content-Type": "application/json"})
    assert status == 409 and "status is now closed" in json.loads(raw)["error"]
    status, raw, _ = _request(server, "GET", "/api/issues/zzzzz")
    assert status == 400 and "no issue matches" in json.loads(raw)["error"]
    status, raw, _ = _request(server, "PATCH", "/api/issues/aaaaa", b"{not json",
                              {"Content-Type": "application/json"})
    assert status == 400 and "invalid JSON" in json.loads(raw)["error"]


def test_http_refuses_cross_site_shapes(server: BoardServer, board_config: Config) -> None:
    _make(board_config, "pl-aaaaa")
    # A form post from another page: no JSON content type.
    status, _raw, _ = _request(server, "POST", "/api/issues/aaaaa/comments", b"body=x",
                               {"Content-Type": "application/x-www-form-urlencoded"})
    assert status == 415
    # DNS rebinding: this server reached under another host name.
    status, _raw, _ = _request(server, "GET", "/api/board", headers={"Host": "evil.example"})
    assert status == 403
    assert not load_comments(board_config, "pl-aaaaa")


def test_serve_needs_an_author_before_listening(
        board_config: Config, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.chdir(board_config.root)
    monkeypatch.delenv("PUNCH_AUTHOR", raising=False)
    monkeypatch.setattr(ops, "git_user", lambda _root: None)
    assert main(["serve", "--no-browser", "--port", "0"]) == 1
    assert "no author: pass --author" in capsys.readouterr().err
