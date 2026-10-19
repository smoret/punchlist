"""`punch check`: validate every issue file and report every problem, not just the first.

Per file it reports the first parse error (the parser stops there), then
checks across files: each `id:` matches its filename, which also makes ids
unique; each `parent` names an existing issue file; and no chain of parents
loops back on itself.
"""

from __future__ import annotations

from pathlib import Path

from .config import Config
from .errors import PunchError
from .issue import Issue, read_issue


def check_issues(config: Config) -> tuple[int, list[PunchError]]:
    """The number of issue files, and every problem found in them, in file and line order."""
    if not config.issues_path.is_dir():
        return 0, []
    paths: list[Path] = sorted(config.issues_path.glob("*.md"))
    # Parents are checked against filenames, so a corrupt parent file is one
    # problem rather than one per child.
    stems: set[str] = {path.stem for path in paths}
    problems: list[PunchError] = []
    parsed: dict[str, Issue] = {}
    for path in paths:
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
    problems.extend(_parent_loops(parsed))
    problems.sort(key=lambda p: (p.path or Path(), p.line or 0))
    return len(paths), problems


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
