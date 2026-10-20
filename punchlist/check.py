"""`punch check`: validate every issue file and report every problem, not just the first.

Per file it reports a file outside its bucket and the first parse error (the
parser stops there), then checks across files: each `id:` matches its
filename, which with correct buckets also makes ids unique; each `parent`
names an existing issue file; and no chain of parents loops back on itself.
Opt-in (config `check_references`, or `punch check --references`): each id
of this tracker's shape mentioned in a body names an existing issue file.
"""

from __future__ import annotations

import re
from pathlib import Path

from .config import Config
from .errors import PunchError
from .ids import ALPHABET
from .issue import Issue, issue_files, placement_error, read_issue


def parent_loop(config: Config, issue_id: str, parent: str) -> list[str] | None:
    """The loop that giving `issue_id` this `parent` would close, as `[a, b, a]`; else None.

    Walks up from `parent` through each ancestor's file. A dangling parent or
    a loop elsewhere ends the walk; `punch check` reports those.
    """
    chain: list[str] = [issue_id]
    node: str | None = parent
    while node is not None and node not in chain[1:]:
        chain.append(node)
        if node == issue_id:
            return chain
        path: Path = config.issue_path(node)
        if not path.is_file():
            return None
        node = read_issue(path, config).text("parent")
    return None


def check_issues(config: Config,
                 references: bool | None = None) -> tuple[int, list[PunchError]]:
    """The number of issue files, and every problem found in them, in file and line order.

    `references` turns the body reference check on or off; None follows the
    config's `check_references`.
    """
    if references is None:
        references = config.check_references
    paths: list[Path] = issue_files(config)
    # Parents are checked against filenames, so a corrupt parent file is one
    # problem rather than one per child.
    stems: set[str] = {path.stem for path in paths}
    problems: list[PunchError] = []
    parsed: dict[str, Issue] = {}
    for path in paths:
        placement: PunchError | None = placement_error(path, config)
        if placement is not None:
            problems.append(placement)
        try:
            issue: Issue = read_issue(path, config)
        except PunchError as exc:
            problems.append(exc)
            continue
        parsed[path.stem] = issue
        if issue.id != path.stem:
            problems.append(PunchError(
                f"id {issue.id!r} does not match the filename", path, issue.lines["id"]
            ))
        parent: str | None = issue.text("parent")
        if parent is not None and parent not in stems:
            problems.append(PunchError(
                f"parent {parent!r} does not exist", path, issue.lines["parent"]
            ))
        if references:
            problems.extend(_dangling_references(config, issue, stems))
    problems.extend(_parent_loops(parsed))
    problems.sort(key=lambda p: (p.path or Path(), p.line or 0))
    return len(paths), problems


def _dangling_references(config: Config, issue: Issue, stems: set[str]) -> list[PunchError]:
    """Each body mention of this tracker's id shape that names no issue file, once per line.

    Only `<prefix>-` plus `id_length` id-alphabet characters, on word
    boundaries, counts, so another tracker's ids (another prefix) never match.
    """
    pattern: re.Pattern[str] = re.compile(
        rf"\b{re.escape(config.prefix)}-[{ALPHABET}]{{{config.id_length}}}\b"
    )
    problems: list[PunchError] = []
    for offset, line in enumerate(issue.body.split("\n")):
        for mention in dict.fromkeys(pattern.findall(line)):
            if mention not in stems:
                problems.append(PunchError(
                    f"reference to {mention}, which does not exist",
                    issue.path, issue.body_line + offset,
                ))
    return problems


def _parent_loops(issues: dict[str, Issue]) -> list[PunchError]:
    """One problem per parent loop, at the `parent:` line of the loop's lowest id."""
    parents: dict[str, str] = {}
    for stem, issue in issues.items():
        parent: str | None = issue.text("parent")
        if parent is not None:
            parents[stem] = parent
    problems: list[PunchError] = []
    done: set[str] = set()
    for start in sorted(parents):
        chain: list[str] = []
        node: str | None = start
        while node is not None and node not in done and node not in chain:
            chain.append(node)
            node = parents.get(node)
        if node is not None and node in chain:
            loop: list[str] = chain[chain.index(node):]
            first: int = loop.index(min(loop))
            loop = loop[first:] + loop[:first]
            head: Issue = issues[loop[0]]
            problems.append(PunchError(
                f"parent loop: {' -> '.join([*loop, loop[0]])}", head.path, head.lines["parent"]
            ))
        done.update(chain)
    return problems
