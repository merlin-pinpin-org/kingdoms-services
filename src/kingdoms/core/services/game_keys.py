"""Game keys: the restricted vocabulary that scopes every game reference.

A game key is the short slug identifying a game across the whole
platform (``aoe2`` today). Ids embed it (``ladder-<game_key>-<guild>``),
so the charset is restricted to lowercase alphanumerics — no separator
ambiguity in ids, no case drift between Discord and the database.

Every entry point that introduces a game key (YAML seeding, ladder
creation) validates through :func:`validate_game_key` and fails closed.
"""

from __future__ import annotations

import re

GAME_KEY_PATTERN = re.compile(r"^[a-z0-9]+$")


def validate_game_key(game_key: str) -> str:
    """Validate a game key (``a-z0-9`` only); raise ValueError when invalid."""
    if not GAME_KEY_PATTERN.fullmatch(game_key or ""):
        raise ValueError(
            f"invalid game key {game_key!r}: must be lowercase alphanumeric (a-z0-9), non-empty"
        )
    return game_key
