"""Issue ids: `<prefix>-<n chars>` in lowercase Crockford base32, drawn with `secrets`.

A draw that names an existing issue file is redrawn, so ids never collide
within one checkout. Two unmerged branches can still mint the same id; that
surfaces as a git add/add conflict.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable

from .config import Config
from .errors import PunchError

# Crockford base32, lowercased: digits and letters minus i, l, o, u.
ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"

# Redraws before giving up. Only reachable when the id space is nearly full.
MAX_DRAWS = 100


def new_id(config: Config, draw: Callable[[str], str] = secrets.choice) -> str:
    """A fresh id with no issue file yet. `draw` picks one character from a string."""
    for _ in range(MAX_DRAWS):
        suffix: str = "".join(draw(ALPHABET) for _ in range(config.id_length))
        candidate: str = f"{config.prefix}-{suffix}"
        if not (config.issues_path / f"{candidate}.md").exists():
            return candidate
    raise PunchError(f"no free id after {MAX_DRAWS} draws; raise id_length in the config")


def resolve_id(config: Config, text: str) -> str:
    """The id of the one issue file whose name starts with `text`, like git's short hashes.

    The `<prefix>-` part may be left off: `7k3` finds `pl-7k3qx`. Only filenames
    are consulted, so an unrelated corrupt file can't block the lookup.
    """
    if not text:
        raise PunchError("empty issue id")
    stems: list[str] = sorted(p.stem for p in config.issues_path.glob("*.md"))
    if text in stems:
        return text
    starts: tuple[str, ...] = (text, f"{config.prefix}-{text}")
    matches: list[str] = [stem for stem in stems if stem.startswith(starts)]
    if not matches:
        raise PunchError(f"no issue matches {text!r}")
    if len(matches) > 1:
        raise PunchError(f"{text!r} is ambiguous: {', '.join(matches)}")
    return matches[0]
