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
        if self.path is None:
            return self.message
        if self.line is None:
            return f"{self.path}: {self.message}"
        return f"{self.path}:{self.line}: {self.message}"
