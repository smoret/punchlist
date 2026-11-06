"""`punch serve`: a local kanban board over the issue files.

`handle` maps one request (method, path, JSON body) to a status and a JSON-able
reply, with no sockets, so it is tested directly; `make_server` wraps it in the
stdlib HTTP server. Every write goes through `ops`, so the board can't write a
file the CLI or `punch check` would reject. The files stay the only database:
each request reloads the config and reads the files, and `version` is a
fingerprint of them that the page polls to pick up edits made elsewhere.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import threading
from dataclasses import dataclass
from decimal import Decimal
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

from .comment import Comment, load_comments, write_comment
from .config import CONFIG_NAME, DEPENDS_ON, Config, load_config
from .errors import PunchError
from .ids import resolve_id
from .issue import Issue, load_issues, read_issue
from .ops import (
    Assignment, Conflict, create_issue, is_blocked, json_fields, set_body, set_fields, sort_key,
)

# A request body larger than this is refused; issue text is small.
MAX_BODY = 1_000_000

# Fields the page may pass to `create`; the rest come from the tracker (`id`, `created`).
CREATE_FIELDS: tuple[str, ...] = ("title", "type", "status", "priority", "rank", "category",
                                  "parent")


class HttpError(Exception):
    """A request refused before it reaches the issues: bad route, method, or body."""

    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status: HTTPStatus = status
        self.message: str = message


@dataclass(frozen=True)
class Board:
    """What every request needs: where the tracker is, and who the page's comments are by."""

    root: Path
    author: str
    # Serializes the board's own writes; a move can touch several files.
    lock: threading.Lock


def handle(board: Board, method: str, path: str, body: Any) -> tuple[HTTPStatus, Any]:
    """One API request -> (status, JSON-able reply). Raises PunchError or HttpError."""
    config: Config = load_config(board.root)
    parts: list[str] = [p for p in path.split("/") if p]
    if parts[:1] != ["api"]:
        raise HttpError(HTTPStatus.NOT_FOUND, f"no such page: {path}")
    route: list[str] = parts[1:]
    if route == ["board"] and method == "GET":
        return HTTPStatus.OK, board_json(config, board.author)
    if route == ["version"] and method == "GET":
        return HTTPStatus.OK, {"version": version(config)}
    if route == ["issues"] and method == "POST":
        with board.lock:
            issue: Issue = _create(config, _object(body))
        return HTTPStatus.CREATED, {"id": issue.id, "version": version(config)}
    if len(route) >= 2 and route[0] == "issues":
        issue_id: str = resolve_id(config, route[1])
        rest: list[str] = route[2:]
        if not rest and method == "GET":
            return HTTPStatus.OK, issue_json(config, issue_id)
        if not rest and method == "PATCH":
            request: dict[str, Any] = _object(body)
            if ("set" in request) == ("body" in request):
                raise HttpError(HTTPStatus.BAD_REQUEST, "send either set or body")
            with board.lock:
                if "body" in request:
                    expected: Any = request.get("expect_body")
                    if expected is not None and not isinstance(expected, str):
                        raise HttpError(HTTPStatus.BAD_REQUEST, "expect_body must be text")
                    set_body(config, issue_id, _text(request, "body"), expected)
                else:
                    set_fields(config, issue_id, _assignments(request.get("set")),
                               _expect(request.get("expect")))
            return HTTPStatus.OK, {"id": issue_id, "version": version(config)}
        if rest == ["move"] and method == "POST":
            with board.lock:
                move(config, issue_id, _object(body))
            return HTTPStatus.OK, {"id": issue_id, "version": version(config)}
        if rest == ["comments"] and method == "POST":
            text: str = _text(_object(body), "body").replace("\r\n", "\n").strip("\n")
            if not text.strip():
                raise PunchError("comment is empty")
            read_issue(config.issue_path(issue_id), config)  # refuse a corrupt issue
            with board.lock:
                write_comment(config, issue_id, board.author, text + "\n",
                              datetime.datetime.now(datetime.timezone.utc))
            return HTTPStatus.CREATED, {"id": issue_id, "version": version(config)}
    raise HttpError(HTTPStatus.NOT_FOUND, f"no route for {method} {path}")


def board_json(config: Config, author: str) -> dict[str, Any]:
    """Everything the page draws: the tracker's choices, and every issue with what a card
    shows beyond its header (blocked, comment count)."""
    issues: list[Issue] = sorted(load_issues(config), key=lambda i: sort_key(config, i))
    statuses: dict[str, str] = {issue.id: issue.status for issue in issues}
    rows: list[dict[str, Any]] = []
    for issue in issues:
        row: dict[str, Any] = {**json_fields(issue, config), "body": issue.body,
                               "comments": _comment_count(config, issue.id)}
        if config.has_links:
            row["blocked"] = is_blocked(issue, statuses)
            row["waiting"] = [d for d in issue.items(DEPENDS_ON) if statuses.get(d) != "closed"]
        rows.append(row)
    return {
        "config": {
            "prefix": config.prefix, "fields": list(config.fields),
            "list_fields": list(config.list_fields), "types": list(config.types),
            "statuses": list(config.statuses), "priorities": list(config.priorities),
            "categories": list(config.categories),
        },
        "name": config.root.name,
        "author": author,
        "issues": rows,
        "version": version(config),
    }


def issue_json(config: Config, issue_id: str) -> dict[str, Any]:
    """One issue for the detail view: header, body, the issues it blocks, and comments."""
    issue: Issue = read_issue(config.issue_path(issue_id), config)
    row: dict[str, Any] = {**json_fields(issue, config), "body": issue.body}
    if config.has_links:
        row["blocks"] = sorted(
            other.id for other in load_issues(config) if issue_id in other.items(DEPENDS_ON))
    comments: list[Comment] = load_comments(config, issue_id)
    row["comments"] = [{"author": c.author, "created": c.created, "body": c.body}
                       for c in comments]
    return row


def move(config: Config, issue_id: str, request: dict[str, Any]) -> None:
    """Drop a card into a lane: set its status, and a rank that puts it just above
    `before` (a card id), or at the end of the lane when `before` is null.

    `expect_status` is the lane the page saw the card in; if the file says otherwise,
    someone else moved it first and nothing is written (`Conflict`).
    """
    status: str = _text(request, "status")
    expected: str = _text(request, "expect_status")
    before: Any = request.get("before")
    if before is not None and not isinstance(before, str):
        raise HttpError(HTTPStatus.BAD_REQUEST, "before must be an issue id or null")
    error: str | None = config.choice_error("status", status)
    if error is not None:
        raise PunchError(error)
    issues: list[Issue] = load_issues(config)
    moved: Issue = next(i for i in issues if i.id == issue_id)
    if moved.status != expected:
        raise Conflict(f"{issue_id} status is now {moved.status}, not {expected}")
    ranks: dict[str, str] = place(config, issues, moved, status,
                                  resolve_id(config, before) if before else None)
    changes: list[Assignment] = [("rank", "", ranks.pop(issue_id))] if issue_id in ranks else []
    if status != moved.status:
        changes.append(("status", "", status))
    if changes:
        set_fields(config, issue_id, changes, {"status": expected})
    for other, rank in ranks.items():
        set_fields(config, other, [("rank", "", rank)])


def place(config: Config, issues: list[Issue], moved: Issue, status: str,
          before: str | None) -> dict[str, str]:
    """The rank changes that put `moved` in lane `status` just above `before` (None: last).

    A lane sorts by priority, then rank (`sort_key`), so a card can only move among the
    cards of its own priority; a drop among another priority's cards lands at the
    nearest place in its own. The moved card gets a rank between its new neighbors, so
    one file changes. When the card above it has no rank, nothing fits between; then the
    whole group is renumbered 1, 2, 3, ... in its new order. No change when the order
    would stay the same.
    """
    if "rank" not in config.fields or before == moved.id:
        return {}
    lane: list[Issue] = sorted((i for i in issues if i.status == status and i.id != moved.id),
                               key=lambda i: sort_key(config, i))
    level: str | None = moved.text("priority")
    group: list[Issue] = [i for i in lane if i.text("priority") == level]
    stop: int = next((n for n, i in enumerate(lane) if i.id == before), len(lane))
    index: int = sum(1 for i in lane[:stop] if i.text("priority") == level)
    order: list[Issue] = group[:index] + [moved] + group[index:]
    if moved.status == status:
        current: list[Issue] = sorted([*group, moved], key=lambda i: sort_key(config, i))
        if [i.id for i in current] == [i.id for i in order]:
            return {}
    above: Issue | None = order[index - 1] if index > 0 else None
    below: Issue | None = order[index + 1] if index + 1 < len(order) else None
    low: Decimal | None = _rank(above)
    high: Decimal | None = _rank(below)
    if above is None:
        new: Decimal = high - 1 if high is not None else Decimal(1)
    elif low is None or (high is not None and low >= high):
        # Nothing fits between an unranked card (or two equal ranks) and the next.
        return {issue.id: str(n) for n, issue in enumerate(order, 1)}
    elif high is None:
        new = low + 1
    else:
        new = (low + high) / 2
    return {moved.id: _decimal_text(new)}


def version(config: Config) -> str:
    """A fingerprint of the config and every file under the issues directory: name, size,
    and modification time. Any edit, by the board or anyone else, changes it."""
    digest = hashlib.sha1()
    paths: list[Path] = [config.root / CONFIG_NAME]
    for folder, _dirs, files in os.walk(config.issues_path):
        paths += [Path(folder, name) for name in files]
    for path in sorted(paths):
        try:
            stat: os.stat_result = path.stat()
        except OSError:
            continue  # removed while walking; the next poll sees the new state
        digest.update(f"{path.relative_to(config.root).as_posix()}\0{stat.st_size}\0"
                      f"{stat.st_mtime_ns}\n".encode())
    return digest.hexdigest()[:16]


def _create(config: Config, request: dict[str, Any]) -> Issue:
    unknown: list[str] = sorted(set(request) - {*CREATE_FIELDS, "labels", "body"})
    if unknown:
        raise HttpError(HTTPStatus.BAD_REQUEST, f"unknown keys: {', '.join(unknown)}")
    fields: dict[str, str] = {name: _text(request, name) for name in CREATE_FIELDS
                              if request.get(name) not in (None, "")}
    labels: list[str] = _strings(request.get("labels"), "labels")
    body: str = request.get("body") or ""
    if not isinstance(body, str):
        raise HttpError(HTTPStatus.BAD_REQUEST, "body must be a string")
    body = body.replace("\r\n", "\n").strip("\n")
    issue, _partners = create_issue(config, fields, labels=labels,
                                    body=body + "\n" if body.strip() else "")
    return issue


def _assignments(raw: Any) -> list[Assignment]:
    """A PATCH's `set`: {field: text, or a list for a list field, or null to remove}."""
    if not isinstance(raw, dict) or not raw:
        raise HttpError(HTTPStatus.BAD_REQUEST, "set must be an object of fields")
    found: list[Assignment] = []
    for name, value in raw.items():
        if value is None:
            value = ""
        elif isinstance(value, list):
            value = ", ".join(_strings(value, name))
        elif not isinstance(value, str):
            raise HttpError(HTTPStatus.BAD_REQUEST, f"{name} must be text, a list, or null")
        found.append((name, "", value))
    return found


def _expect(raw: Any) -> dict[str, str | None]:
    if raw is None:
        return {}
    if not isinstance(raw, dict) or not all(
            isinstance(v, str) or v is None for v in raw.values()):
        raise HttpError(HTTPStatus.BAD_REQUEST, "expect must map fields to text or null")
    return dict(raw)


def _object(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise HttpError(HTTPStatus.BAD_REQUEST, "expected a JSON object")
    return body


def _text(request: dict[str, Any], key: str) -> str:
    value: Any = request.get(key)
    if not isinstance(value, str):
        raise HttpError(HTTPStatus.BAD_REQUEST, f"{key} must be text")
    return value


def _strings(value: Any, key: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise HttpError(HTTPStatus.BAD_REQUEST, f"{key} must be a list of text")
    return value


def _rank(issue: Issue | None) -> Decimal | None:
    text: str | None = issue.text("rank") if issue else None
    return Decimal(text) if text is not None else None


def _decimal_text(value: Decimal) -> str:
    """A rank as the plain decimal the header format takes: no exponent, no trailing zeros."""
    text: str = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _comment_count(config: Config, issue_id: str) -> int:
    folder: Path = config.comments_path(issue_id)
    return sum(1 for p in folder.iterdir() if p.suffix == ".md") if folder.is_dir() else 0


class BoardServer(ThreadingHTTPServer):
    """The HTTP server for one board; requests are handled by `_Handler`."""

    daemon_threads = True

    def __init__(self, board: Board, host: str, port: int) -> None:
        super().__init__((host, port), _Handler)
        self.board: Board = board
        bound: int = self.server_address[1]
        self.url: str = f"http://{host}:{bound}/"
        # A page on another site can make a browser send requests here; one that names
        # this server by another host name (DNS rebinding) is refused.
        self.hosts: set[str] = {f"{name}:{bound}" for name in (host, "localhost", "127.0.0.1")}


def make_server(root: Path, author: str, host: str = "127.0.0.1", port: int = 0) -> BoardServer:
    """A server for the tracker at `root`, not yet serving; port 0 picks a free one."""
    return BoardServer(Board(root, author, threading.Lock()), host, port)


class _Handler(BaseHTTPRequestHandler):
    @property
    def owner(self) -> BoardServer:
        """The server this request came to; `_Handler` is only ever made by a BoardServer."""
        return cast(BoardServer, self.server)

    def do_GET(self) -> None:
        """The page itself, or an API read."""
        if urlsplit(self.path).path == "/":
            if self._host_ok():
                page: bytes = resources.files("punchlist").joinpath("static") \
                    .joinpath("board.html").read_bytes()
                self._send(HTTPStatus.OK, page, "text/html; charset=utf-8")
            return
        self._api("GET")

    def do_POST(self) -> None:
        """Create an issue, move one, or comment."""
        self._api("POST")

    def do_PATCH(self) -> None:
        """Set an issue's fields."""
        self._api("PATCH")

    def log_message(self, format: str, *args: Any) -> None:
        """Quiet: the page polls every few seconds, and a log line per poll is noise."""

    def _host_ok(self) -> bool:
        if self.headers.get("Host", "") in self.owner.hosts:
            return True
        self._json(HTTPStatus.FORBIDDEN, {"error": "unexpected Host header"})
        return False

    def _api(self, method: str) -> None:
        if not self._host_ok():
            return
        try:
            body: Any = None
            if method != "GET":
                # A form on another site can POST here, but only a page on this one can
                # send JSON (a cross-site one needs a preflight this server never grants).
                if not self.headers.get("Content-Type", "").startswith("application/json"):
                    raise HttpError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                                    "send Content-Type: application/json")
                length: int = int(self.headers.get("Content-Length") or 0)
                if length > MAX_BODY:
                    raise HttpError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request too large")
                try:
                    body = json.loads(self.rfile.read(length).decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise HttpError(HTTPStatus.BAD_REQUEST, f"invalid JSON: {exc}") from None
            status, reply = handle(self.owner.board, method, urlsplit(self.path).path, body)
            self._json(status, reply)
        except HttpError as exc:
            self._json(exc.status, {"error": exc.message})
        except Conflict as exc:
            self._json(HTTPStatus.CONFLICT, {"error": exc.describe(self.owner.board.root)})
        except PunchError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": exc.describe(self.owner.board.root)})

    def _json(self, status: HTTPStatus, reply: Any) -> None:
        self._send(status, json.dumps(reply, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _send(self, status: HTTPStatus, data: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)
