"""`punch check`: validate every issue file and report every problem, not just the first.

Per file it reports a file outside its bucket and the first parse error (the
parser stops there), then checks across files: each `id:` matches its
filename, which with correct buckets also makes ids unique; each `parent`
names an existing issue file; and no chain of parents loops back on itself.
With a `depends-on` field: each dependency names an existing issue file, and
no group of issues depends on each other in a cycle. With a `related` field:
each related id names another existing issue, which lists this one back.
Opt-in (config `check_references`, or `punch check --references`): each id
of this tracker's shape mentioned in a body names an existing issue file.
Comments: every folder beside an issue file belongs to an existing issue, and
every file in a `comments/` folder is a valid comment matching its name.
"""

from __future__ import annotations

import re
from pathlib import Path

from .comment import read_comment
from .config import DEPENDS_ON, RELATED, Config
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


def dependency_loop(config: Config, issue_id: str,
                    dependencies: list[str]) -> list[str] | None:
    """The loop giving `issue_id` these dependencies would close, as `[a, b, a]`; else None.

    Searches breadth-first from each dependency through the `depends-on` of
    each issue file it reaches, so the loop reported is a shortest one. A
    missing file ends that branch; `punch check` reports it.
    """
    previous: dict[str, str] = {}
    queue: list[str] = []
    for dependency in dependencies:
        if dependency == issue_id:
            return [issue_id, issue_id]
        if dependency not in previous:
            previous[dependency] = issue_id
            queue.append(dependency)
    while queue:
        node: str = queue.pop(0)
        path: Path = config.issue_path(node)
        if not path.is_file():
            continue
        for successor in read_issue(path, config).items(DEPENDS_ON):
            if successor == issue_id:
                return _loop_path(previous, issue_id, node)
            if successor not in previous:
                previous[successor] = node
                queue.append(successor)
    return None


def _loop_path(previous: dict[str, str], start: str, last: str) -> list[str]:
    """`[start, ..., last, start]` from the breadth-first `previous` links."""
    path: list[str] = [last]
    while path[0] != start:
        path.insert(0, previous[path[0]])
    return [*path, start]


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
        if config.has_links:
            for dependency in issue.items(DEPENDS_ON):
                if dependency not in stems:
                    problems.append(PunchError(
                        f"{DEPENDS_ON} {dependency!r} does not exist", path,
                        issue.lines[DEPENDS_ON],
                    ))
        if config.has_related:
            for other in issue.items(RELATED):
                if other == issue.id:
                    problems.append(PunchError(
                        f"{RELATED} lists the issue itself", path, issue.lines[RELATED]
                    ))
                elif other not in stems:
                    problems.append(PunchError(
                        f"{RELATED} {other!r} does not exist", path, issue.lines[RELATED]
                    ))
        if references:
            problems.extend(_dangling_references(config, issue, stems))
    problems.extend(_parent_loops(parsed))
    if config.has_links:
        problems.extend(_dependency_loops(parsed))
    if config.has_related:
        problems.extend(_one_sided_related(parsed))
    problems.extend(_comment_problems(config))
    problems.sort(key=lambda p: (p.path or Path(), p.line or 0))
    return len(paths), problems


def _comment_problems(config: Config) -> list[PunchError]:
    """Problems with comment folders and files.

    A folder beside an issue file (`issues/<bucket>/<id>/`) must belong to an
    existing, correctly placed issue; every entry of its `comments/` folder
    must be a comment file whose header parses and matches its name.
    """
    problems: list[PunchError] = []
    if not config.issues_path.is_dir():
        return problems
    for folder in sorted(config.issues_path.glob("*/*/")):
        try:
            expected: Path = config.issue_path(folder.name).with_suffix("")
        except PunchError:
            expected = Path()
        if folder != expected or not (folder.parent / f"{folder.name}.md").is_file():
            problems.append(PunchError(
                f"folder for issue {folder.name!r}, which does not exist here", folder
            ))
            continue
        comments: Path = folder / "comments"
        if not comments.is_dir():
            continue
        for path in sorted(comments.iterdir()):
            if not path.is_file() or path.suffix != ".md":
                problems.append(PunchError(
                    "not a comment file (expected <time>-<author>.md)", path
                ))
                continue
            try:
                read_comment(path)
            except PunchError as exc:
                problems.append(exc)
    return problems


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


def _one_sided_related(issues: dict[str, Issue]) -> list[PunchError]:
    """A related link the other issue doesn't list back, on the side that has it.

    `set` and `new` write both sides, so this comes from a hand edit or a merge
    that kept one file's side. A partner that didn't parse is skipped; its own
    problem is already reported.
    """
    problems: list[PunchError] = []
    for stem, issue in issues.items():
        for other in issue.items(RELATED):
            if other != stem and other in issues and stem not in issues[other].items(RELATED):
                problems.append(PunchError(
                    f"{RELATED} {other!r} does not list {stem!r} back", issue.path,
                    issue.lines[RELATED],
                ))
    return problems


def _dependency_loops(issues: dict[str, Issue]) -> list[PunchError]:
    """One problem per group of issues that depend on each other in a cycle.

    Each strongly connected component with a cycle is reported once, at the
    `depends-on` line of its lowest id, naming a shortest loop through it.
    """
    graph: dict[str, list[str]] = {
        stem: [d for d in issue.items(DEPENDS_ON) if d in issues]
        for stem, issue in issues.items()
    }
    problems: list[PunchError] = []
    for component in _components(graph):
        start: str = min(component)
        if len(component) == 1 and start not in graph[start]:
            continue
        members: set[str] = set(component)
        previous: dict[str, str] = {}
        queue: list[str] = [start]
        last: str | None = None
        while queue and last is None:
            node: str = queue.pop(0)
            for successor in sorted(graph[node]):
                if successor == start:
                    last = node
                    break
                if successor in members and successor not in previous:
                    previous[successor] = node
                    queue.append(successor)
        assert last is not None  # a component with a cycle has a loop through any member
        head: Issue = issues[start]
        loop: list[str] = _loop_path(previous, start, last)
        problems.append(PunchError(
            f"dependency loop: {' -> '.join(loop)}", head.path, head.lines[DEPENDS_ON]
        ))
    return problems


def _components(graph: dict[str, list[str]]) -> list[list[str]]:
    """Strongly connected components of `graph` (Tarjan's algorithm, iterative)."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[list[str]] = []
    for root in sorted(graph):
        if root in index:
            continue
        work: list[tuple[str, int]] = [(root, 0)]
        while work:
            node, position = work[-1]
            if position == 0:
                index[node] = low[node] = len(index)
                stack.append(node)
                on_stack.add(node)
            successors: list[str] = graph[node]
            if position < len(successors):
                work[-1] = (node, position + 1)
                successor: str = successors[position]
                if successor not in index:
                    work.append((successor, 0))
                elif successor in on_stack:
                    low[node] = min(low[node], index[successor])
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[node])
            if low[node] == index[node]:
                component: list[str] = []
                while True:
                    member: str = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                components.append(component)
    return components
