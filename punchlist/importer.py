"""`punch import-md`: turn a checklist-style markdown roadmap into issues.

The shapes it reads, by line:

- `# Title` and the prose under it: the file's intro; skipped.
- `## Section` sets the category (slugified); `### Subsection` adds a label.
- `- [ ] text`: a task.
- `- text` with nested bullets and no checkbox: an epic.
- `- text` with no checkbox and no children: a note, folded into its parent's body.
- An indented paragraph or table after a blank line: a continuation of the
  item it is indented under (markdown's list-item rule), added to its body.

Nested bullets get `parent:` set to the nearest enclosing task or epic. The
title is the first `**bold**` span, or for a plain item the text before the
first ` -- `, `: `, `; `, or sentence end, cut to 100 characters. The body
is the item's full text, verbatim, then its continuations and notes, with
relative links rewritten from the source file's directory to the issue's
bucket directory.

It fails closed: anything else -- prose outside an item, a checked or
malformed checkbox, a note with no parent, an unexpected heading, tab
indentation -- is reported with its line, and `plan_import` callers refuse to
write while any remain.
"""

from __future__ import annotations

import os
import re
import secrets
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .ids import new_id
from .issue import FieldValue, Issue

BULLET_RE = re.compile(r"( *)- (.*)")
HEADING_RE = re.compile(r"(#+) (.*)")
CHECKBOX_RE = re.compile(r"\[(.)\] ")
LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")
SCHEME_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:")
# Longer titles are cut at a word boundary; the body keeps the full text.
MAX_TITLE = 100

# Where a plain (non-bold) item's title ends: a dash separator, a colon or
# semicolon, or a sentence end. The earliest one wins.
PLAIN_TITLE_END_RE = re.compile(r" -- |[:;] |(?<=[.!?]) ")


@dataclass
class _Entry:
    """One body line after the item's own: a continuation or a folded note."""

    line: int
    text: str
    blank_before: bool
    note: bool = False


@dataclass
class _Node:
    """One bullet of the roadmap."""

    line: int
    indent: int
    checkbox: bool
    text: str
    category: str | None
    label: str | None
    parent: _Node | None
    entries: list[_Entry] = field(default_factory=list[_Entry])
    children: list[_Node] = field(default_factory=list["_Node"])

    @property
    def content_col(self) -> int:
        """The column a continuation must reach to belong to this bullet."""
        return self.indent + 2

    @property
    def is_note(self) -> bool:
        """A bullet with no checkbox and no children is a note, not an issue."""
        return not self.checkbox and not self.children


@dataclass(frozen=True)
class Unclassified:
    """Source lines `start`..`end` that the importer can't place."""

    start: int
    end: int
    reason: str


@dataclass
class ImportPlan:
    """What an import would write."""

    issues: list[Issue]
    notes: int
    unclassified: list[Unclassified]
    missing_categories: list[str]

    def counts(self, name: str) -> Counter[str]:
        """Planned issues counted by a scalar field; `-` for absent."""
        return Counter(issue.text(name) or "-" for issue in self.issues)


def slugify(name: str) -> str:
    """`Engine and rules` -> `engine-and-rules`."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def item_title(text: str) -> str | None:
    """The first bold span, else the text before the first ` -- `, `: `, `; `, or sentence end."""
    if text.startswith("**"):
        end: int = text.find("**", 2)
        if end <= 2:
            return None
        title: str = text[2:end]
    else:
        title = PLAIN_TITLE_END_RE.split(text, maxsplit=1)[0]
    title = title.strip().rstrip(".:").strip()
    if len(title) > MAX_TITLE:
        title = title[:MAX_TITLE - 3].rsplit(" ", 1)[0] + "..."
    return title or None


def rewrite_links(text: str, source_dir: Path, issues_dir: Path) -> str:
    """Re-aim relative markdown links from `source_dir` to `issues_dir`.

    Absolute URLs, root-relative paths, and `#anchor`-only links are left alone.
    """
    def fix(match: re.Match[str]) -> str:
        label, target = match.group(1), match.group(2)
        if target.startswith(("#", "/")) or SCHEME_RE.match(target):
            return match.group(0)
        path, sep, anchor = target.partition("#")
        new: str = Path(os.path.relpath(source_dir / path, issues_dir)).as_posix()
        if path.endswith("/"):
            new += "/"
        return f"[{label}]({new}{sep}{anchor})"
    return LINK_RE.sub(fix, text)


def _parse(text: str) -> tuple[list[_Node], list[tuple[int, str]]]:
    """Every bullet in document order, and every line that fits no shape."""
    nodes: list[_Node] = []
    problems: list[tuple[int, str]] = []
    stack: list[_Node] = []
    category: str | None = None
    label: str | None = None
    in_intro: bool = False
    blank: bool = False

    for lineno, line in enumerate(text.split("\n"), 1):
        if not line.strip():
            blank = True
            continue
        was_blank, blank = blank, False
        if "\t" in line[:len(line) - len(line.lstrip())]:
            problems.append((lineno, "tab indentation"))
            continue

        heading: re.Match[str] | None = HEADING_RE.fullmatch(line)
        if heading:
            stack.clear()
            level: int = len(heading.group(1))
            name: str = slugify(heading.group(2))
            if level == 1 and lineno == 1:
                in_intro = True
            elif level == 2 and name:
                category, label, in_intro = name, None, False
            elif level == 3 and name and category is not None:
                label = name
            else:
                problems.append((lineno, f"unexpected heading: {line}"))
            continue

        bullet: re.Match[str] | None = BULLET_RE.fullmatch(line)
        if bullet:
            in_intro = False
            indent: int = len(bullet.group(1))
            content: str = bullet.group(2)
            checkbox: re.Match[str] | None = CHECKBOX_RE.match(content)
            if checkbox and checkbox.group(1) != " ":
                problems.append((lineno, f"checkbox [{checkbox.group(1)}] is not [ ]"))
                continue
            while stack and stack[-1].indent >= indent:
                stack.pop()
            parent: _Node | None = stack[-1] if stack else None
            node = _Node(lineno, indent, checkbox is not None,
                         content[checkbox.end():] if checkbox else content,
                         category, label, parent)
            if parent is not None:
                parent.children.append(node)
            nodes.append(node)
            stack.append(node)
            continue

        indent = len(line) - len(line.lstrip(" "))
        while stack and stack[-1].content_col > indent:
            stack.pop()
        if stack:
            owner: _Node = stack[-1]
            owner.entries.append(_Entry(lineno, line[owner.content_col:], was_blank))
        elif not in_intro:
            problems.append((lineno, "prose outside any item"))
    return nodes, problems


def _body(node: _Node, source_dir: Path, issues_dir: Path) -> str:
    entries: list[_Entry] = list(node.entries)
    for child in node.children:
        if child.is_note:
            entries.append(_Entry(child.line, f"- {child.text}", False, note=True))
            entries.extend(_Entry(e.line, f"  {e.text}", e.blank_before, note=True)
                           for e in child.entries)
    lines: list[str] = [node.text]
    previous_note: bool = False
    for entry in sorted(entries, key=lambda e: e.line):
        if entry.blank_before or (entry.note and not previous_note):
            lines.append("")
        lines.append(entry.text)
        previous_note = entry.note
    return rewrite_links("\n".join(lines), source_dir, issues_dir) + "\n"


def _merge(problems: list[tuple[int, str]], text: str) -> list[Unclassified]:
    """Group problem lines with the same reason, separated only by blank lines, into ranges."""
    lines: list[str] = text.split("\n")
    merged: list[Unclassified] = []
    for lineno, reason in sorted(problems):
        if merged:
            last: Unclassified = merged[-1]
            between: list[str] = lines[last.end:lineno - 1]
            if last.reason == reason and all(not s.strip() for s in between):
                merged[-1] = Unclassified(last.start, lineno, reason)
                continue
        merged.append(Unclassified(lineno, lineno, reason))
    return merged


def plan_import(text: str, source: Path, config: Config, today: str,
                draw: Callable[[str], str] = secrets.choice) -> ImportPlan:
    """Read a roadmap's text and plan the issues it becomes. Writes nothing."""
    nodes, problems = _parse(text)
    items: list[_Node] = []
    titles: dict[int, str] = {}
    for node in nodes:
        if node.is_note:
            if node.parent is None:
                problems.append((node.line, "note with no parent item"))
            continue
        title: str | None = item_title(node.text)
        if title is None:
            problems.append((node.line, "no title"))
            continue
        titles[node.line] = title
        items.append(node)

    source_dir: Path = source.resolve().parent
    ids: dict[int, str] = {}
    issues: list[Issue] = []
    for node in items:
        ids[node.line] = new_id(config, draw, ids.values())
        # Links are relative to the file's own directory: its bucket.
        issue_dir: Path = config.issue_path(ids[node.line]).parent
        fields: dict[str, FieldValue] = {
            "id": ids[node.line],
            "title": rewrite_links(titles[node.line], source_dir, issue_dir),
            "type": "task" if node.checkbox else "epic",
            "status": "open",
            "created": today,
        }
        if node.category is not None:
            fields["category"] = node.category
        if node.parent is not None and node.parent.line in ids:
            fields["parent"] = ids[node.parent.line]
        if node.label is not None:
            fields["labels"] = [node.label]
        issues.append(Issue(fields, _body(node, source_dir, issue_dir)))

    needed: list[str] = list(dict.fromkeys(n.category for n in items if n.category))
    return ImportPlan(
        issues=issues,
        notes=sum(1 for n in nodes if n.is_note and n.parent is not None),
        unclassified=_merge(problems, text),
        missing_categories=[c for c in needed if c not in config.categories],
    )
