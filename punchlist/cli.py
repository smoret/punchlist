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
from dataclasses import dataclass
from pathlib import Path

from .check import check_issues, dependency_loop, parent_loop
from .comment import Comment, load_comments, write_comment
from .config import (
    CONFIG_NAME, DEPENDS_ON, RELATED, REQUIRED_FIELDS, Config, add_categories, find_root,
    init_config, load_config,
)
from .errors import PunchError, display_path
from .git import Change, Commit, git_user, log, read_blobs, require_history, resolve_rev
from .history import Entry, Event, classify, is_comment, mentions, owns
from .ids import new_id, resolve_id
from .importer import ImportPlan, plan_import
from .issue import (
    DATE_RE, FieldValue, Issue, edit_header, format_issue, load_issues, parse_issue, rank_error,
    read_issue, read_text, update_issue, write_edits, write_issue,
)

# `list` filters on these scalar fields; each has a flag of the same name.
FILTER_FIELDS: tuple[str, ...] = ("status", "type", "category", "priority", "parent")


def _count(n: int, noun: str) -> str:
    """`1 issue`, `2 issues`."""
    return f"{n} {noun}{'' if n == 1 else 's'}"


def _load() -> Config:
    return load_config(find_root(Path.cwd()))


def _load_all(config: Config) -> list[Issue]:
    # load_issues stops at the first bad file; point at the command that reports them all.
    try:
        return load_issues(config)
    except PunchError as exc:
        raise PunchError(f"{exc.message} (run `punch check` to find corrupt files)",
                         exc.path, exc.line) from exc


def cmd_init(args: argparse.Namespace) -> int:
    """Write a default punchlist.json in the current directory."""
    path: Path = init_config(Path.cwd(), args.prefix)
    print(f"wrote {path}")
    return 0


def cmd_new(args: argparse.Namespace) -> int:
    """Create an issue and print its id."""
    config: Config = _load()
    title: str = args.title.strip()
    if not title:
        raise PunchError("title must not be empty")
    fields: dict[str, str] = {"title": title, "status": "open"}
    for name in ("type", "priority", "rank", "category"):
        value: str | None = getattr(args, name)
        if value is not None:
            fields[name] = value
    if args.parent is not None:
        fields["parent"] = resolve_id(config, args.parent)
    # Checked before writing so the error names the flag, not a file never written.
    for name, value in fields.items():
        if name not in config.fields:
            raise PunchError(f"{name} is not a field in the config")
        error: str | None = config.choice_error(name, value)
        if error is None and name == "rank":
            error = rank_error(value)
        if error is not None:
            raise PunchError(error)
    fields["created"] = datetime.date.today().isoformat()
    fields["id"] = new_id(config)
    issue_fields: dict[str, FieldValue] = dict(fields)
    if args.label:
        if "labels" not in config.list_fields:
            raise PunchError("labels is not a list field in the config")
        labels: list[str] = [item.strip() for label in args.label for item in label.split(",")]
        if not all(labels):
            raise PunchError("empty label")
        issue_fields["labels"] = labels
    if args.depends_on:
        # A new issue can't close a loop: nothing depends on it yet.
        issue_fields[DEPENDS_ON] = _link_ids(config, DEPENDS_ON, args.depends_on)
    partners: list[tuple[str, Path, str]] = []
    if args.related:
        related: list[str] = _link_ids(config, RELATED, args.related)
        issue_fields[RELATED] = related
        partners = _partner_edits(config, fields["id"], [], related)
    issue = Issue(issue_fields, _read_body(args))
    write_issue(issue, config, create=True)
    write_edits([(path, text) for _other, path, text in partners])
    print(issue.id)
    return 0


def _link_ids(config: Config, field: str, values: list[str]) -> list[str]:
    """A link field's comma-separated ids (short ids allowed) -> full ids, in order, no repeats."""
    if field not in config.fields:
        raise PunchError(f"{field} is not a field in the config")
    texts: list[str] = [item.strip() for value in values for item in value.split(",")]
    if not all(texts):
        raise PunchError(f"empty item in {field}")
    return list(dict.fromkeys(resolve_id(config, text) for text in texts))


def _partner_edits(config: Config, issue_id: str, old: list[str],
                   new: list[str]) -> list[tuple[str, Path, str]]:
    """The other side of a related-link change: (partner id, its path, its new text).

    Each newly related issue gains `issue_id`; each one no longer related loses
    it. Partners already in the right state are left out. Computed (and
    validated) before anything is written.
    """
    edits: list[tuple[str, Path, str]] = []
    for other in [o for o in new if o not in old] + [o for o in old if o not in new]:
        path: Path = config.issue_path(other)
        if not path.is_file():
            continue  # a dangling id being removed; check reports dangling ones
        items: list[str] = read_issue(path, config).items(RELATED)
        if other in new and issue_id not in items:
            value: str | None = ", ".join([*items, issue_id])
        elif other not in new and issue_id in items:
            value = ", ".join(i for i in items if i != issue_id) or None
        else:
            continue
        edits.append((other, path, edit_header(read_text(path), config, path, {RELATED: value})))
    return edits


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
    """The `issues` passing the filter flags, sorted by `_sort_key`.

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
    if "status" not in wanted and not args.all:
        wanted["status"] = "open"
    labels: list[str] = args.label or []
    if labels and "labels" not in config.list_fields:
        raise PunchError("labels is not a list field in the config")

    matches: list[Issue] = [
        issue for issue in issues
        if all(issue.text(name) == value for name, value in wanted.items())
        and all(label in issue.items("labels") for label in labels)
    ]
    if args.ready or args.blocked:
        if not config.has_links:
            raise PunchError(f"--ready and --blocked need a {DEPENDS_ON} field in the config")
        statuses: dict[str, str] = {issue.id: issue.status for issue in everything}
        matches = [issue for issue in matches
                   if _is_blocked(issue, statuses) == bool(args.blocked)]
    matches.sort(key=lambda issue: _sort_key(config, issue))
    return matches


def _is_blocked(issue: Issue, statuses: dict[str, str]) -> bool:
    """Whether any dependency isn't closed; a missing one counts, since it can't be done."""
    return any(statuses.get(dependency) != "closed" for dependency in issue.items(DEPENDS_ON))


def _sort_key(config: Config, issue: Issue) -> tuple[int, bool, float, str, str]:
    """Priority in config order, then rank, then created, then id; missing values sort last."""
    priority: str | None = issue.text("priority")
    level: int = config.priorities.index(priority) if priority else len(config.priorities)
    rank: float | None = issue.rank
    return (level, rank is None, rank or 0.0, issue.text("created") or "", issue.id)


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
            row: dict[str, object] = dict(_json_fields(issue, config))
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
        row: dict[str, object] = {**_json_fields(issue, config), "body": issue.body}
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


def _author(flag: str | None, root: Path) -> str:
    """--author, else $PUNCH_AUTHOR, else git's user.name; an error if none is set."""
    author: str = (flag or os.environ.get("PUNCH_AUTHOR") or git_user(root) or "").strip()
    if not author:
        raise PunchError("no author: pass --author, set PUNCH_AUTHOR, or set git's user.name")
    if "\n" in author or "\r" in author:
        raise PunchError("author must be a single line")
    return author


def cmd_history(args: argparse.Namespace) -> int:
    """Print an issue's timeline from git, oldest first: date, commit, what changed, subject."""
    config: Config = _load()
    issue_id: str = resolve_id(config, args.id)
    issue: Issue = read_issue(config.issue_path(issue_id), config)
    require_history(config.root)
    revs: list[str] = []
    since_date: str | None = None
    if args.since is not None:
        if _is_date(args.since):
            since_date = args.since
        else:
            revs = [f"{resolve_rev(config.root, args.since)}..HEAD"]
    entries: list[Entry] = [
        entry for entry in _timeline(config, issue_id, revs)
        if since_date is None or entry.commit.date >= since_date
    ]
    if args.json:
        print(json.dumps([entry.as_json() for entry in entries], ensure_ascii=False))
        return 0
    _print_issues(config, [issue], False)
    described: list[str] = ["; ".join(e.describe() for e in entry.events) for entry in entries]
    width: int = min(max(map(len, described), default=0), MAX_EVENTS_WIDTH)
    for entry, events in zip(entries, described):
        commit: Commit = entry.commit
        print(f"{commit.date}  {commit.sha[:7]}  {events:<{width}}  {commit.subject}")
    return 0


# `history` pads its events column to the widest line, up to this; longer ones push the subject.
MAX_EVENTS_WIDTH = 40


def _is_date(text: str) -> bool:
    """Whether `text` is a YYYY-MM-DD date, rather than a revision."""
    if not DATE_RE.fullmatch(text):
        return False
    try:
        datetime.date.fromisoformat(text)
    except ValueError:
        return False
    return True


def _timeline(config: Config, issue_id: str, revs: list[str]) -> list[Entry]:
    """Every commit in `revs` (default: all of HEAD's history) that changed the issue's file
    or comments, in any bucket, or that mentions it; oldest first."""
    issues_dir: str = Path(config.issues_dir).as_posix()
    touched: list[Commit] = log(config.root, revs, [
        f":(glob){issues_dir}/**/{issue_id}.md", f":(glob){issues_dir}/**/{issue_id}/comments/*",
    ])
    # Each change's file before (at its parent) and after; both fetched in one git call.
    changes: list[tuple[Commit, Change]] = [
        (commit, change) for commit in touched for change in commit.changes
        if owns(change.path, issue_id) or owns(change.old_path, issue_id)
    ]
    specs: list[str] = []
    for commit, change in changes:
        specs.append(f"{commit.sha}^:./{change.old_path}" if change.status != "A" else "")
        specs.append(f"{commit.sha}:./{change.path}" if change.status != "D" else "")
    blobs: list[bytes | None] = read_blobs(config.root, [s for s in specs if s])
    found: dict[str, bytes | None] = dict(zip([s for s in specs if s], blobs))
    events: dict[str, list[Event]] = {}
    for i, (commit, change) in enumerate(changes):
        old: bytes | None = found.get(specs[2 * i])
        new: bytes | None = found.get(specs[2 * i + 1])
        classified: list[Event] = classify(change, old, new, config)
        bucket: list[Event] = events.setdefault(commit.sha, [])
        # The issue file's events before its comments', whatever order git listed them in.
        if is_comment(change.path):
            bucket.extend(classified)
        else:
            bucket[0:0] = classified
    entries: list[Entry] = [
        Entry(issue_id, commit, tuple(events[commit.sha]))
        for commit in touched if events.get(commit.sha)
    ]
    shas: set[str] = {commit.sha for commit in touched}
    short: str = issue_id.partition("-")[2]
    # git's fixed-string grep narrows the search; the configured pattern decides.
    entries += [
        Entry(issue_id, commit, (Event("referenced"),))
        for commit in log(config.root, ["-F", f"--grep={short}", *revs])
        if commit.sha not in shas and mentions(commit, issue_id, config)
    ]
    return sorted(entries, key=lambda entry: entry.commit.time)


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
    path: Path = config.issue_path(issue_id)
    issue: Issue = read_issue(path, config)
    # Everything is validated before writing, so an error names the argument.
    changes: dict[str, str | None] = {}
    for assignment in args.assignments:
        name, sep, raw = assignment.partition("=")
        name, op = name.strip(), ""
        if name.endswith(("+", "-")):
            name, op = name[:-1].strip(), name[-1]
        if not sep:
            raise PunchError(f"expected field=value, got {assignment!r}")
        if name in changes:
            raise PunchError(f"{name} is set twice")
        if name not in config.fields:
            raise PunchError(f"{name} is not a field in the config")
        if name == "id":
            raise PunchError("id cannot be changed; it names the file")
        value: str = raw.strip()
        if op:
            value = _list_after(config, issue, name, op, value)
        changes[name] = _set_value(config, issue_id, name, value)

    changed: list[str] = [
        name for name, value in changes.items() if _field_text(issue, name) != value
    ]
    if not changed:
        print(f"{issue_id} unchanged")
        return 0
    # Compute every file's new text first: a related change also edits partners,
    # and a bad partner must stop the whole change before anything is written.
    text: str = edit_header(read_text(path), config, path, {n: changes[n] for n in changed})
    partners: list[tuple[str, Path, str]] = []
    if RELATED in changed:
        new: str | None = changes[RELATED]
        partners = _partner_edits(config, issue_id, issue.items(RELATED),
                                  new.split(", ") if new else [])
    write_edits([(path, text), *((p, t) for _o, p, t in partners)])
    # A list's `=` replaces it, so say what went: a dropped link is easy to miss.
    notes: list[str] = []
    for name in changed:
        if name in config.list_fields:
            kept: str | None = changes[name]
            removed: list[str] = [i for i in issue.items(name)
                                  if i not in (kept.split(", ") if kept else [])]
            notes.append(f"{name} (removed {', '.join(removed)})" if removed else name)
        else:
            notes.append(name)
    print(f"updated {issue_id}: {', '.join(notes)}")
    for other, _path, _text in partners:
        print(f"updated {other}: {RELATED}")
    return 0


def _list_after(config: Config, issue: Issue, name: str, op: str, value: str) -> str:
    """The whole list `field+=value` or `field-=value` leaves, as `=` would take it.

    Adding an id already there or removing one that isn't changes nothing, so a
    retry is safe. `-=` matches a stored id exactly before resolving it, so a
    link to a deleted issue can still be removed.
    """
    if name not in config.list_fields:
        raise PunchError(f"{name}{op}= only works on list fields")
    current: list[str] = issue.items(name)
    texts: list[str] = [item.strip() for item in value.split(",")]
    if not all(texts):
        raise PunchError(f"empty item in {name}{op}=")
    links: bool = name in (DEPENDS_ON, RELATED)
    if op == "+":
        added: list[str] = _link_ids(config, name, texts) if links else texts
        return ", ".join(dict.fromkeys([*current, *added]))
    gone: set[str] = {
        text if text in current or not links else resolve_id(config, text) for text in texts
    }
    return ", ".join(item for item in current if item not in gone)


def _set_value(config: Config, issue_id: str, name: str, value: str) -> str | None:
    """The header text `punch set` writes for one assignment; None removes the field."""
    if not value:
        if name in REQUIRED_FIELDS:
            raise PunchError(f"{name} is required and cannot be removed")
        return None
    if name == DEPENDS_ON:
        dependencies: list[str] = _link_ids(config, DEPENDS_ON, [value])
        cycle: list[str] | None = dependency_loop(config, issue_id, dependencies)
        if cycle is not None:
            raise PunchError(f"dependency loop: {' -> '.join(cycle)}")
        return ", ".join(dependencies)
    if name == RELATED:
        related: list[str] = _link_ids(config, RELATED, [value])
        if issue_id in related:
            raise PunchError(f"{issue_id} can't be related to itself")
        return ", ".join(related)
    if name in config.list_fields:
        items: list[str] = [item.strip() for item in value.split(",")]
        if not all(items):
            raise PunchError(f"empty item in {name}")
        return ", ".join(items)
    error: str | None = config.choice_error(name, value)
    if error is None and name == "rank":
        error = rank_error(value)
    if error is not None:
        raise PunchError(error)
    if name == "parent":
        value = resolve_id(config, value)
        loop: list[str] | None = parent_loop(config, issue_id, value)
        if loop is not None:
            raise PunchError(f"parent loop: {' -> '.join(loop)}")
    return value


def _field_text(issue: Issue, name: str) -> str | None:
    """A field as its header text (list fields joined), or None if absent."""
    value: FieldValue | None = issue.fields.get(name)
    return ", ".join(value) if isinstance(value, list) else value


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


def _json_fields(issue: Issue, config: Config) -> dict[str, FieldValue]:
    """An issue's header fields in config order, for --json output."""
    return {name: issue.fields[name] for name in config.fields if name in issue.fields}


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


def _add_filters(parser: argparse.ArgumentParser) -> None:
    """The filter and output flags `list` and `search` share; `_select` reads them."""
    statuses = parser.add_mutually_exclusive_group()
    statuses.add_argument("--status", help="only this status (default: open)")
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
    parser.add_argument("--json", action="store_true", help="a JSON array of header fields")


def build_parser() -> argparse.ArgumentParser:
    """The argument parser for every subcommand."""
    parser = argparse.ArgumentParser(prog="punch", description="A file-based issue tracker.")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help=f"write a default {CONFIG_NAME} here")
    init.add_argument("--prefix", required=True, help="issue id prefix, e.g. pl for pl-7k3qx")
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
        "list", help="list open issues: id status type priority category title"
    )
    _add_filters(lst)
    lst.set_defaults(func=cmd_list)

    search = sub.add_parser(
        "search", help="list open issues whose title or body matches a regex"
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
    history.add_argument("--json", action="store_true",
                         help="a JSON array: per commit its sha, date, author, subject, events")
    history.set_defaults(func=cmd_history)

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
