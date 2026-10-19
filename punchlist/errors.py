"""The one exception type the CLI reports. Anything else escaping is a bug."""

from __future__ import annotations

from pathlib import Path


class PunchError(Exception):
    """A user-facing error. `path` and `line` locate it when it comes from a file."""

    def __init__(self, message: str, path: Path | None = None, line: int | None = None) -> None:
        self.message: str = message
        self.path: Path | None = path
        self.line: int | None = line
        super().__init__(str(self))

    def __str__(self) -> str:
        return self.describe()

    def describe(self, base: Path | None = None) -> str:
        """`path:line: message`, with the path relative to `base` when it lies under it."""
        if self.path is None:
            return self.message
        shown: Path = self.path
        if base is not None and shown.is_relative_to(base):
            shown = shown.relative_to(base)
        if self.line is None:
            return f"{shown.as_posix()}: {self.message}"
        return f"{shown.as_posix()}:{self.line}: {self.message}"
