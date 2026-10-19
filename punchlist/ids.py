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
