"""Ladder ids: the visible, guild-scoped identity of a ladder.

A ladder id is human-readable and embedded in every UI footer:
``ladder-<game_key>-<guild_id>`` (game keys restricted to ``a-z0-9``
by :mod:`kingdoms.core.services.game_keys`, guild ids being Discord
snowflakes, the composite is unambiguous). Seasons append their
incremental index: ``<ladder_id>-<index>`` (see the core SeasonService).
"""

from __future__ import annotations

from kingdoms.core.services.game_keys import validate_game_key


def ladder_id(game_key: str, guild_id: str) -> str:
    """Build the visible ladder id ``ladder-<game_key>-<guild_id>``."""
    validate_game_key(game_key)
    guild = (guild_id or "").strip()
    if not guild:
        raise ValueError("a ladder is guild-scoped: guild_id is required")
    return f"ladder-{game_key}-{guild}"
