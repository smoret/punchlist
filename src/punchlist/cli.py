"""The `punch` command line. Each subcommand is a `cmd_<name>(args) -> int` function.

Output is terse because agents pay for every token. Errors are one line on
stderr and exit status 1.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

from .check import check_issues
from .comment import Comment, load_comments, write_comment
from .config import (
    CONFIG_NAME, DEPENDS_ON, LINE_ENDINGS, RELATED, Config, add_categories, find_root,
    init_config, load_config,
)
from .errors import PunchError, display_path
from .git import Change, Commit, log, read_blobs, require_history, resolve_rev
from .history import (
    CHANGE_GROUPS, Entry, Event, change_groups, classify, is_comment, issue_of, mentions, owns,
)
from .ids import resolve_id
from .importer import ImportPlan, plan_import
from .issue import (
    DATE_RE, Issue, format_issue, load_issues, parse_issue, read_issue, read_text,
    update_issue, write_issue,
)
from .ops import (
    Assignment, SetResult, create_issue, is_blocked, json_fields, resolve_author, set_fields,
    sort_key,
)
from .web import BoardServer, make_server

# `serve` listens here unless told otherwise: fixed, so a bookmark keeps working.
DEFAULT_PORT = 8314

# `list` filters on these scalar fields; each has a flag of the same name.
FILTER_FIELDS: tuple[str, ...] = ("status", "type", "category", "priority", "parent")


def _count(n: int, noun: str) -> str:
    """`1 issue`, `2 issues`."""
    return f"{n} {noun}{'' if n == 1 else 's'}"


def _load() -> Config:
    return load_config(find_root(Path.cwd()))


def _author(flag: str | None, root: Path) -> str:
    """--author, else $PUNCH_AUTHOR, else git's user.name (see `resolve_author`)."""
    return resolve_author(flag, os.environ.get("PUNCH_AUTHOR"), root)


def _load_all(config: Config) -> list[Issue]:
    # load_issues stops at the first bad file; point at the command that reports them all.
    try:
        return load_issues(config)
    except PunchError as exc:
        raise PunchError(f"{exc.message} (run `punch check` to find corrupt files)",
                         exc.path, exc.line) from exc


def cmd_init(args: argparse.Namespace) -> int:
    """Write a default punchlist.json in the current directory."""
    path: Path = init_config(Path.cwd(), args.prefix, args.line_endings)
    print(f"wrote {path}")
    return 0


def cmd_new(args: argparse.Namespace) -> int:
    """Create an issue and print its id."""
    config: Config = _load()
    fields: dict[str, str] = {"title": args.title}
    for name in ("type", "priority", "rank", "category", "parent"):
        value: str | None = getattr(args, name)
        if value is not None:
            fields[name] = value
    issue, _partners = create_issue(config, fields, labels=args.label,
                                    depends_on=args.depends_on, related=args.related,
                                    body=_read_body(args))
    print(issue.id)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    """Print matching issues, one line each: id status type priority category title."""
    config: Config = _load()
    everything: list[Issue] = _load_all(config)
    _print_issues(config, _select(config, everything, args, everything), args.json)
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    """Like `list`, but only issues whose title or body matches a regex."""
    config: Config = _load()
    try:
        pattern: re.Pattern[str] = re.compile(
            args.pattern, 0 if args.case_sensitive else re.IGNORECASE
        )
    except re.error as exc:
        raise PunchError(f"invalid regex {args.pattern!r}: {exc}") from None
    everything: list[Issue] = _load_all(config)
    hits: list[Issue] = [
        issue for issue in everything
        if pattern.search(issue.title) or pattern.search(issue.body)
    ]
    selected: list[Issue] = _select(config, hits, args, everything)
    snippets: dict[str, list[Snippet]] | None = None
    if args.context is not None:
        snippets = {issue.id: _snippets(issue, pattern, args.context) for issue in selected}
    _print_issues(config, selected, args.json, snippets)
    return 0


@dataclass(frozen=True)
class Snippet:
    """One body line shown under a search hit: its file line number, and whether it matched."""

    line: int
    text: str
    match: bool


# Longer body lines are cut to a window around the match.
MAX_SNIPPET = 160


def _snippets(issue: Issue, pattern: re.Pattern[str], context: int) -> list[Snippet]:
    """Every body line matching `pattern`, plus `context` lines either side, in order."""
    lines: list[str] = issue.body.split("\n")
    if lines and not lines[-1]:
        lines.pop()  # the body's final newline, not a line
    matched: set[int] = {i for i, line in enumerate(lines) if pattern.search(line)}
    shown: set[int] = {
        j for i in matched for j in range(max(0, i - context), min(len(lines), i + context + 1))
    }
    return [
        Snippet(issue.body_line + i, _clip(lines[i], pattern), i in matched)
        for i in sorted(shown)
    ]


def _clip(line: str, pattern: re.Pattern[str]) -> str:
    """`line`, or a MAX_SNIPPET window of it around the match, with `...` where it was cut."""
    if len(line) <= MAX_SNIPPET:
        return line
    found: re.Match[str] | None = pattern.search(line)
    start: int = max(0, found.start() - MAX_SNIPPET // 3) if found else 0
    start = min(start, len(line) - MAX_SNIPPET)
    end: int = start + MAX_SNIPPET
    return ("..." if start else "") + line[start:end] + ("..." if end < len(line) else "")


def _select(config: Config, issues: list[Issue], args: argparse.Namespace,
            everything: list[Issue]) -> list[Issue]:
    """The `issues` passing the filter flags, sorted by `sort_key`.

    `everything` is every issue in the tracker, for --ready and --blocked to
    look up dependencies that aren't themselves among `issues`.
    """
    wanted: dict[str, str] = {}
    for name in FILTER_FIELDS:
        value: str | None = getattr(args, name)
        if value is None:
            continue
        error: str | None = config.choice_error(name, value)
        if error is not None:
            raise PunchError(error)
        wanted[name] = resolve_id(config, value) if name == "parent" else value
    # Without --status or --all, everything still in play: a workflow status a tracker adds
    # (`progress`, say) is as open as `open`.
    unfinished: bool = "status" not in wanted and not args.all
    labels: list[str] = args.label or []
    if labels and "labels" not in config.list_fields:
        raise PunchError("labels is not a list field in the config")

    matches: list[Issue] = [
        issue for issue in issues
        if not (unfinished and issue.status == "closed")
        and all(issue.text(name) == value for name, value in wanted.items())
        and all(label in issue.items("labels") for label in labels)
    ]
    if args.ready or args.blocked:
        if not config.has_links:
            raise PunchError(f"--ready and --blocked need a {DEPENDS_ON} field in the config")
        statuses: dict[str, str] = {issue.id: issue.status for issue in everything}
        matches = [issue for issue in matches
                   if is_blocked(issue, statuses) == bool(args.blocked)]
    matches.sort(key=lambda issue: sort_key(config, issue))
    return matches


def _print_issues(config: Config, issues: list[Issue], as_json: bool,
                  snippets: dict[str, list[Snippet]] | None = None) -> None:
    """One line per issue (id status type priority category title), or a JSON array.

    With `snippets`, each issue's lines follow it grep-style: `  12: match`,
    `  13- context`, and `  --` between groups that aren't adjacent; in JSON
    they are a `matches` array.
    """
    if as_json:
        rows: list[dict[str, object]] = []
        for issue in issues:
            row: dict[str, object] = dict(json_fields(issue, config))
            if snippets is not None:
                row["matches"] = [
                    {"line": s.line, "text": s.text, "match": s.match} for s in snippets[issue.id]
                ]
            rows.append(row)
        print(json.dumps(rows, ensure_ascii=False))
        return
    for issue in issues:
        columns: list[str] = [issue.id] + [
            issue.text(name) or "-" for name in ("status", "type", "priority", "category")
        ]
        print(" ".join(columns), issue.title)
        previous: int | None = None
        for snippet in (snippets or {}).get(issue.id, []):
            if previous is not None and snippet.line > previous + 1:
                print("  --")
            print(f"  {snippet.line}{':' if snippet.match else '-'} {snippet.text}")
            previous = snippet.line


def cmd_show(args: argparse.Namespace) -> int:
    """Print one issue's file, or with --json its fields plus `body`; --comments adds those."""
    config: Config = _load()
    issue_id: str = resolve_id(config, args.id)
    path: Path = config.issue_path(issue_id)
    text: str = read_text(path)
    issue: Issue = parse_issue(text, config, path)
    comments: list[Comment] = load_comments(config, issue_id) if args.comments else []
    if args.json:
        row: dict[str, object] = {**json_fields(issue, config), "body": issue.body}
        if config.has_links:
            # Derived, never stored: the issues listing this one in depends-on.
            row["blocks"] = sorted(
                other.id for other in _load_all(config) if issue_id in other.items(DEPENDS_ON)
            )
        if args.comments:
            row["comments"] = [
                {"author": c.author, "created": c.created, "body": c.body} for c in comments
            ]
        print(json.dumps(row, ensure_ascii=False))
        return 0
    sys.stdout.write(text)
    if comments:
        sys.stdout.write("\n## Comments\n")
        for comment in comments:
            sys.stdout.write(f"\n### {comment.author}, {comment.created}\n\n{comment.body}")
    return 0


def cmd_comment(args: argparse.Namespace) -> int:
    """Add a comment to an issue and print the new file's path."""
    config: Config = _load()
    issue_id: str = resolve_id(config, args.id)
    read_issue(config.issue_path(issue_id), config)  # refuse to comment on a corrupt issue
    author: str = _author(args.author, config.root)
    body: str = _read_body(args)
    if not body:
        raise PunchError("comment body is empty; pass --body or --body-file")
    path: Path = write_comment(config, issue_id, author, body,
                               datetime.datetime.now(datetime.timezone.utc))
    print(display_path(path, Path.cwd().resolve()))
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    """Print an issue's timeline from git, oldest first: date, commit, what changed, subject."""
    config: Config = _load()
    issue_id: str = resolve_id(config, args.id)
    issues: list[Issue] = [read_issue(config.issue_path(issue_id), config)]
    if args.tree:
        issues += _descendants(config, _load_all(config), issue_id)
    revs, since_date = _since(config, args.since)
    entries: list[Entry] = [
        entry for entry in _timeline(config, [issue.id for issue in issues], revs)
        if since_date is None or entry.commit.date >= since_date
    ]
    if args.json:
        print(json.dumps([entry.as_json(args.diff) for entry in entries], ensure_ascii=False))
        return 0
    _print_issues(config, issues, False)
    described: list[str] = ["; ".join(e.describe() for e in entry.events) for entry in entries]
    width: int = min(max(map(len, described), default=0), MAX_EVENTS_WIDTH)
    for entry, events in zip(entries, described):
        commit: Commit = entry.commit
        named: str = f"{entry.issue_id}  " if args.tree else ""
        print(f"{commit.date}  {commit.sha[:7]}  {named}{events:<{width}}  {commit.subject}")
        if args.diff:
            for event in entry.events:
                for line in event.diff or ():
                    print(f"    {line}")
    return 0


def cmd_changed(args: argparse.Namespace) -> int:
    """Print the issues created, closed, reopened, or rescoped in a range of history, grouped
    by what happened: `date sha id title`, a line per commit."""
    config: Config = _load()
    revs, since_date = _since(config, args.since)
    everything: list[Issue] = _load_all(config)
    selected: dict[str, Issue] = {
        issue.id: issue for issue in _select(config, everything, args, everything)
    }
    issues_dir: str = Path(config.issues_dir).as_posix()
    commits: list[Commit] = log(config.root, revs, [
        f":(glob){issues_dir}/*.md", f":(glob){issues_dir}/*/*.md",
    ])
    groups: dict[str, list[Entry]] = {group: [] for group in CHANGE_GROUPS}
    for entry in _entries(config, commits, lambda path: (
            issue_of(path) if issue_of(path) in selected else None)):
        if since_date is None or entry.commit.date >= since_date:
            for group in change_groups(entry.events):
                groups[group].append(entry)
    if args.json:
        print(json.dumps({group: [entry.as_json() for entry in entries]
                          for group, entries in groups.items()}, ensure_ascii=False))
        return 0
    if not any(groups.values()):
        print(f"nothing changed since {args.since}")
    for group, entries in groups.items():
        if entries:
            print(f"{group} ({len(entries)})")
        for entry in entries:
            commit: Commit = entry.commit
            print(f"  {commit.date}  {commit.sha[:7]}  {entry.issue_id}  "
                  f"{selected[entry.issue_id].title}")
    return 0


def _descendants(config: Config, everything: list[Issue], issue_id: str) -> list[Issue]:
    """Every issue under `issue_id` by parent links, depth first, each issue's children in
    `sort_key` order; a parent loop ends where it comes back round."""
    children: dict[str, list[Issue]] = {}
    for issue in everything:
        parent: str | None = issue.text("parent")
        if parent is not None:
            children.setdefault(parent, []).append(issue)
    found: list[Issue] = []
    seen: set[str] = {issue_id}

    def visit(parent: str) -> None:
        for child in sorted(children.get(parent, []), key=lambda i: sort_key(config, i)):
            if child.id not in seen:
                seen.add(child.id)
                found.append(child)
                visit(child.id)

    visit(issue_id)
    return found


# `history` pads its events column to the widest line, up to this; longer ones push the subject.
MAX_EVENTS_WIDTH = 40


def _since(config: Config, since: str | None) -> tuple[list[str], str | None]:
    """A --since value as (git log revision args, date to filter by); refuses where git
    history can't be read.

    A YYYY-MM-DD date keeps commits authored on or after it: git narrows by commit date
    from the day before (time zones), and the date filter decides. Anything else is a
    revision, and keeps the commits after it.
    """
    require_history(config.root)
    if since is None:
        return [], None
    if not DATE_RE.fullmatch(since):
        return [f"{resolve_rev(config.root, since)}..HEAD"], None
    try:
        day: datetime.date = datetime.date.fromisoformat(since)
    except ValueError:
        raise PunchError(f"{since!r} is not a real date") from None
    return [f"--since={day - datetime.timedelta(days=1)}"], since


def _timeline(config: Config, ids: list[str], revs: list[str]) -> list[Entry]:
    """Every commit in `revs` (default: all of HEAD's history) that changed one of the issues'
    files or comments, in any bucket, or that mentions one; oldest first, then in `ids` order.

    However many issues, three git calls: one log of the commits touching them, one fetch
    of every changed file before and after, and one log of the commits mentioning them.
    """
    issues_dir: str = Path(config.issues_dir).as_posix()
    touched: list[Commit] = log(config.root, revs, [
        spec for issue_id in ids for spec in (
            f":(glob){issues_dir}/**/{issue_id}.md",
            f":(glob){issues_dir}/**/{issue_id}/comments/*")
    ])
    entries: list[Entry] = _entries(
        config, touched, lambda path: next((i for i in ids if owns(path, i)), None))
    # A commit that touched an issue's files is never also `referenced` by it, even when
    # those changes amount to no event.
    touching: set[tuple[str, str]] = {
        (issue_id, commit.sha) for commit in touched for change in commit.changes
        for issue_id in ids if owns(change.path, issue_id) or owns(change.old_path, issue_id)
    }
    # git's fixed-string grep narrows the search (several --grep match any); the
    # configured pattern decides.
    greps: list[str] = [f"--grep={issue_id.partition('-')[2]}" for issue_id in ids]
    entries += [
        Entry(issue_id, commit, (Event("referenced"),))
        for commit in log(config.root, ["-F", *greps, *revs]) for issue_id in ids
        if (issue_id, commit.sha) not in touching and mentions(commit, issue_id, config)
    ]
    return sorted(entries, key=lambda entry: (entry.commit.time, ids.index(entry.issue_id)))


def _entries(config: Config, commits: list[Commit],
             owner: Callable[[str], str | None]) -> list[Entry]:
    """Each commit's classified events per issue, oldest first; `owner` names the issue a
    file path belongs to, or None to skip it. A commit whose changes amount to no event
    (a comment moved with its issue) has no entry."""
    changes: list[tuple[str, Commit, Change]] = []
    for commit in commits:
        for change in commit.changes:
            new_owner: str | None = owner(change.path)
            old_owner: str | None = owner(change.old_path)
            if new_owner == old_owner:
                if new_owner is not None:
                    changes.append((new_owner, commit, change))
                continue
            # git paired one issue's deleted file with another's new one as a rename: two
            # issues, so two changes.
            if old_owner is not None:
                changes.append((old_owner, commit, Change("D", change.old_path, change.old_path)))
            if new_owner is not None:
                changes.append((new_owner, commit, Change("A", change.path, change.path)))
    specs: list[str] = []
    for _id, commit, change in changes:
        specs.append(f"{commit.sha}^:./{change.old_path}" if change.status != "A" else "")
        specs.append(f"{commit.sha}:./{change.path}" if change.status != "D" else "")
    blobs: list[bytes | None] = read_blobs(config.root, [s for s in specs if s])
    found: dict[str, bytes | None] = dict(zip([s for s in specs if s], blobs))
    events: dict[tuple[str, str], list[Event]] = {}
    for i, (issue_id, commit, change) in enumerate(changes):
        old: bytes | None = found.get(specs[2 * i])
        new: bytes | None = found.get(specs[2 * i + 1])
        classified: list[Event] = classify(change, old, new, config)
        bucket: list[Event] = events.setdefault((issue_id, commit.sha), [])
        # The issue file's events before its comments', whatever order git listed them in.
        if is_comment(change.path):
            bucket.extend(classified)
        else:
            bucket[0:0] = classified
    by_sha: dict[str, Commit] = {commit.sha: commit for commit in commits}
    return [Entry(issue_id, by_sha[sha], tuple(found_events))
            for (issue_id, sha), found_events in events.items() if found_events]


def cmd_close(args: argparse.Namespace) -> int:
    """Set an issue's status to closed, changing only that line; --comment says why."""
    if args.author is not None and args.comment is None:
        raise PunchError("--author only applies with --comment")
    config: Config = _load()
    path: Path = config.issue_path(resolve_id(config, args.id))
    issue: Issue = read_issue(path, config)
    if issue.status == "closed":
        # Idempotent: a retried close must not add the same comment twice.
        print(f"{issue.id} already closed" + ("; comment not added" if args.comment else ""))
        return 0
    if args.comment is not None:
        body: str = args.comment.strip("\n")
        if not body.strip():
            raise PunchError("--comment is empty")
        # Comment first: if it fails, the issue is not left closed without its reason.
        comment_path: Path = write_comment(
            config, issue.id, _author(args.author, config.root), body + "\n",
            datetime.datetime.now(datetime.timezone.utc),
        )
        print(display_path(comment_path, Path.cwd().resolve()))
    update_issue(path, config, {"status": "closed"})
    print(f"closed {issue.id}")
    return 0


def cmd_set(args: argparse.Namespace) -> int:
    """Set or remove header fields: `field=value` (a list field's whole list), `field=` to
    remove an optional field, and `field+=a,b` / `field-=a` to add to or remove from a list."""
    config: Config = _load()
    issue_id: str = resolve_id(config, args.id)
    assignments: list[Assignment] = []
    for assignment in args.assignments:
        name, sep, raw = assignment.partition("=")
        name, op = name.strip(), ""
        if name.endswith(("+", "-")):
            name, op = name[:-1].strip(), name[-1]
        if not sep:
            raise PunchError(f"expected field=value, got {assignment!r}")
        assignments.append((name, op, raw))
    result: SetResult = set_fields(config, issue_id, assignments)
    if not result.changed:
        print(f"{issue_id} unchanged")
        return 0
    # A list's `=` replaces it, so say what went: a dropped link is easy to miss.
    notes: list[str] = [
        f"{name} (removed {', '.join(removed)})" if removed else name
        for name, removed in result.changed
    ]
    print(f"updated {issue_id}: {', '.join(notes)}")
    for other in result.partners:
        print(f"updated {other}: {RELATED}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Print every problem in every issue file, one per line; exit 1 if there are any."""
    config: Config = _load()
    count, problems = check_issues(config, references=True if args.references else None)
    for problem in problems:
        print(problem.describe(Path.cwd().resolve()))
    if problems:
        return 1
    print(f"{_count(count, 'issue')} ok")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    """Serve the kanban board on this machine until interrupted."""
    config: Config = _load()
    # Resolved now, not at the first comment: a board that can't sign comments says so
    # before it opens.
    author: str = _author(args.author, config.root)
    try:
        server: BoardServer = make_server(config.root, author, port=args.port)
    except OSError as exc:
        raise PunchError(f"cannot listen on port {args.port}: {exc.strerror} "
                         "(pass --port to pick another, 0 for any free one)") from None
    print(f"serving {display_path(config.root)} at {server.url} as {author}; Ctrl+C stops",
          flush=True)
    if not args.no_browser:
        webbrowser.open(server.url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def cmd_import_md(args: argparse.Namespace) -> int:
    """Import a markdown roadmap as issues; --dry-run reports what it would create."""
    config: Config = _load()
    source = Path(args.file)
    try:
        text: str = read_text(source)
    except OSError as exc:
        raise PunchError(f"cannot read: {exc.strerror}", source) from None
    plan: ImportPlan = plan_import(text, source, config, datetime.date.today().isoformat())

    verb: str = "would create" if args.dry_run else "creating"
    types: str = ", ".join(f"{n} {t}" for t, n in sorted(plan.counts("type").items()))
    print(f"{verb} {_count(len(plan.issues), 'issue')} ({types}); "
          f"{_count(plan.notes, 'note')} folded into parents")
    for category, count in sorted(plan.counts("category").items()):
        print(f"  {category}: {count}")
    shown: str = source.as_posix()
    if plan.unclassified:
        print("unclassified lines (move them, or pass --skip-unclassified):")
        for item in plan.unclassified:
            span: str = str(item.start) if item.start == item.end else f"{item.start}-{item.end}"
            print(f"  {shown}:{span}: {item.reason}")
    if plan.missing_categories:
        print(f"categories missing from {CONFIG_NAME} (add them with --add-categories): "
              + ", ".join(plan.missing_categories))
    if not plan.ranked:
        print(f"source order not kept: add \"rank\" to fields in {CONFIG_NAME} to keep it")

    blocked: bool = (bool(plan.unclassified) and not args.skip_unclassified) or (
        bool(plan.missing_categories) and not args.add_categories)
    if args.dry_run:
        return 1 if blocked else 0
    if blocked:
        raise PunchError("nothing imported")

    if plan.missing_categories:
        config = add_categories(config.root, plan.missing_categories)
    for issue in plan.issues:  # validate every issue before writing any
        parse_issue(format_issue(issue, config), config, config.issue_path(issue.id))
    for issue in plan.issues:
        write_issue(issue, config, create=True)
    print(f"created {_count(len(plan.issues), 'issue')}")
    return 0


def _read_body(args: argparse.Namespace) -> str:
    """The body from --body or --body-file (`-` for stdin), trimmed of blank edges."""
    text: str
    if args.body is not None:
        text = args.body
    elif args.body_file == "-":
        text = sys.stdin.read()
    elif args.body_file is not None:
        path = Path(args.body_file)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise PunchError(f"cannot read body file: {exc.strerror}", path) from None
    else:
        return ""
    text = text.replace("\r\n", "\n").strip("\n")
    return text + "\n" if text.strip() else ""


def _context_lines(text: str) -> int:
    """argparse type for --context: a whole number, 0 or more."""
    if not text.isdigit():
        raise argparse.ArgumentTypeError(f"expected a whole number 0 or more, got {text!r}")
    return int(text)


def _add_filters(parser: argparse.ArgumentParser, any_status: bool = False) -> None:
    """The filter flags `list`, `search`, and `changed` share; `_select` reads them.

    `any_status` makes every status the default rather than all but closed, with no --all.
    """
    if any_status:
        parser.add_argument("--status", help="only issues with this status now (default: any)")
        parser.set_defaults(all=True)
    else:
        statuses = parser.add_mutually_exclusive_group()
        statuses.add_argument("--status", help="only this status (default: any but closed)")
        statuses.add_argument("--all", action="store_true", help="every status")
    parser.add_argument("--type")
    parser.add_argument("--category")
    parser.add_argument("--priority")
    parser.add_argument("--parent", help="only children of this issue id")
    parser.add_argument("--label", action="append", help="repeatable; issues must have every label")
    readiness = parser.add_mutually_exclusive_group()
    readiness.add_argument("--ready", action="store_true",
                           help=f"only issues whose {DEPENDS_ON} issues are all closed")
    readiness.add_argument("--blocked", action="store_true",
                           help=f"only issues waiting on a {DEPENDS_ON} issue that isn't closed")


def build_parser() -> argparse.ArgumentParser:
    """The argument parser for every subcommand."""
    parser = argparse.ArgumentParser(prog="punch", description="A file-based issue tracker.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {version('punchlist')}")
    sub =parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help=f"write a default {CONFIG_NAME} here")
    init.add_argument("--prefix", required=True, help="issue id prefix, e.g. pl for pl-7k3qx")
    init.add_argument("--line-endings", choices=list(LINE_ENDINGS), default="platform",
                      help="line endings of every file punch writes (default: the platform's)")
    init.set_defaults(func=cmd_init)

    new = sub.add_parser("new", help="create an issue; prints its id")
    new.add_argument("title")
    new.add_argument("--type", required=True)
    new.add_argument("--priority")
    new.add_argument("--rank", help="sort key within a priority: a number like 2 or 2.5")
    new.add_argument("--category")
    new.add_argument("--parent", help="id of the parent issue")
    new.add_argument("--depends-on", action="append",
                     help="id of an issue this one waits on; repeatable, commas also separate")
    new.add_argument("--related", action="append",
                     help="id of a related issue (also linked back); repeatable, commas separate")
    new.add_argument("--label", action="append", help="repeatable; commas also separate labels")
    body = new.add_mutually_exclusive_group()
    body.add_argument("--body", help="body text")
    body.add_argument("--body-file", help="read the body from a file, or `-` for stdin")
    new.set_defaults(func=cmd_new)

    lst = sub.add_parser(
        "list", help="list unclosed issues: id status type priority category title"
    )
    _add_filters(lst)
    lst.add_argument("--json", action="store_true", help="a JSON array of header fields")
    lst.set_defaults(func=cmd_list)

    search = sub.add_parser(
        "search", help="list unclosed issues whose title or body matches a regex"
    )
    search.add_argument(
        "pattern",
        help=r"a Python regex, case-insensitive unless --case-sensitive; \bword\b for whole words",
    )
    search.add_argument("--case-sensitive", action="store_true")
    search.add_argument(
        "--context", type=_context_lines, metavar="N",
        help="under each hit, its matching body lines with line numbers and N lines either side",
    )
    _add_filters(search)
    search.add_argument("--json", action="store_true", help="a JSON array of header fields")
    search.set_defaults(func=cmd_search)

    show = sub.add_parser("show", help="print an issue")
    show.add_argument("id", help="an issue id, or any unique start of one")
    show.add_argument("--json", action="store_true", help="header fields plus body as JSON")
    show.add_argument("--comments", action="store_true",
                      help="also print its comments, oldest first")
    show.set_defaults(func=cmd_show)

    comment = sub.add_parser("comment", help="add a comment to an issue; prints its path")
    comment.add_argument("id", help="an issue id, or any unique start of one")
    comment.add_argument("--author",
                         help="who is commenting (default: $PUNCH_AUTHOR, then git's user.name)")
    comment_body = comment.add_mutually_exclusive_group()
    comment_body.add_argument("--body", help="comment text")
    comment_body.add_argument("--body-file", help="read the comment from a file, or `-` for stdin")
    comment.set_defaults(func=cmd_comment)

    history = sub.add_parser(
        "history", help="an issue's timeline from git: date, commit, what changed, subject",
        description="Every commit that changed the issue's file or comments, or whose message "
                    "mentions its id (config mention_pattern), oldest first.",
    )
    history.add_argument("id", help="an issue id, or any unique start of one")
    history.add_argument("--since", metavar="DATE|REV",
                         help="only commits on or after a YYYY-MM-DD date, or after a revision")
    history.add_argument("--diff", action="store_true",
                         help="each body change as a unified diff under its line")
    history.add_argument("--tree", action="store_true",
                         help="also every issue under it by parent links, each line naming its "
                              "issue")
    history.add_argument("--json", action="store_true",
                         help="a JSON array: per commit its sha, date, author, subject, events")
    history.set_defaults(func=cmd_history)

    changed = sub.add_parser(
        "changed", help="issues created, closed, reopened, or rescoped since a date or revision",
        description="Read from git: each issue whose file a commit in the range created, "
                    "closed, reopened, or rescoped (its title or body), grouped by what "
                    "happened, a line per commit. Filters apply to the issues as they are now.",
    )
    changed.add_argument("since", metavar="DATE|REV",
                         help="a YYYY-MM-DD date (commits on or after it), or a revision "
                              "(commits after it)")
    _add_filters(changed, any_status=True)
    changed.add_argument("--json", action="store_true",
                         help="a JSON object: per group, records like `history --json` gives")
    changed.set_defaults(func=cmd_changed)

    close = sub.add_parser("close", help="set an issue's status to closed")
    close.add_argument("id", help="an issue id, or any unique start of one")
    close.add_argument("--comment", help="also add this comment, e.g. why it was closed")
    close.add_argument("--author",
                       help="the comment's author (default: $PUNCH_AUTHOR, then git's user.name)")
    close.set_defaults(func=cmd_close)

    set_ = sub.add_parser(
        "set", help="set header fields: field=value, field= to remove, list+=id, list-=id",
        description="field=value sets a field; for a list field (labels, depends-on, related) "
                    "the value is the WHOLE list and replaces it. field= removes an optional "
                    "field. list+=a,b adds to a list field and list-=a removes from it, "
                    "leaving the rest. Removed list items are printed.",
    )
    set_.add_argument("id", help="an issue id, or any unique start of one")
    set_.add_argument("assignments", nargs="+", metavar="field=value",
                      help="field=value, field=, list+=a,b, or list-=a")
    set_.set_defaults(func=cmd_set)

    check = sub.add_parser("check", help="validate every issue file; exit 1 on any problem")
    check.add_argument(
        "--references", action="store_true",
        help="also flag body mentions of ids that don't exist (on by default if the config "
             "sets check_references)",
    )
    check.set_defaults(func=cmd_check)

    serve = sub.add_parser(
        "serve", help="a kanban board of the issues in your browser, on this machine only",
        description="Serves a board at http://127.0.0.1:PORT/: a lane per status in config "
                    "order, cards ordered by priority then rank. Changes are written to the "
                    "issue files like `punch set`; nothing is committed.",
    )
    serve.add_argument("--port", type=int, default=DEFAULT_PORT,
                       help=f"port to listen on (default: {DEFAULT_PORT}; 0 picks a free one)")
    serve.add_argument("--author",
                       help="who the board's comments are by (default: $PUNCH_AUTHOR, then "
                            "git's user.name)")
    serve.add_argument("--no-browser", action="store_true", help="don't open a browser")
    serve.set_defaults(func=cmd_serve)

    imp = sub.add_parser("import-md", help="import a markdown roadmap's checklist as issues")
    imp.add_argument("file")
    imp.add_argument("--dry-run", action="store_true",
                     help="report what would be created; exit 1 if a real run would refuse")
    imp.add_argument("--add-categories", action="store_true",
                     help=f"add categories the import needs to {CONFIG_NAME}")
    imp.add_argument("--skip-unclassified", action="store_true",
                     help="import anyway, leaving out lines that fit no shape")
    imp.set_defaults(func=cmd_import_md)

    return parser


def _configure_stdio() -> None:
    # Issue text is UTF-8; on Windows, redirected stdio defaults to cp1252.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """Entry point for the `punch` and `punchlist` console scripts."""
    _configure_stdio()
    args: argparse.Namespace = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except PunchError as exc:
        print(f"punch: error: {exc.describe(Path.cwd().resolve())}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
