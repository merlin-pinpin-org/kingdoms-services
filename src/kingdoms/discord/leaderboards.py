"""Leaderboard selection: which provider boards a guild displays (#147).

The provider serves every board it knows (ext-librematch: rm_1v1,
rm_team, unranked, dm_1v1, dm_team); a guild picks 1 to 4 of them.
The choice lives in the guild settings (``leaderboards``), defaulting
to RM 1v1 + TG RM.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("kingdoms.leaderboards")

AVAILABLE_BOARDS: tuple[tuple[str, str], ...] = (
    ("rm_1v1", "RM 1v1"),
    ("rm_team", "TG RM"),
    ("unranked", "Unranked"),
    ("dm_1v1", "DM 1v1"),
    ("dm_team", "TG DM"),
)

DEFAULT_BOARDS: tuple[str, ...] = ("rm_1v1", "rm_team")
MAX_BOARDS = 4


async def get_guild_boards(guild_id: str, db: Any) -> list[str]:
    """Read the guild's chosen boards (ordered, validated, 1-4)."""
    boards = DEFAULT_BOARDS
    try:
        settings = await db.get_guild_settings(guild_id) if db else None
        raw = (settings or {}).get("leaderboards")
        if isinstance(raw, (list, tuple)) and raw:
            valid = [b for b in raw if any(k == b for k, _ in AVAILABLE_BOARDS)]
            if valid:
                boards = valid[:MAX_BOARDS]
    except Exception:
        logger.warning("leaderboards setting read failed — defaulting", exc_info=True)
    return list(boards)


async def set_guild_boards(guild_id: str, boards: list[str], db: Any) -> list[str]:
    """Persist the guild's boards; returns the stored (validated) list."""
    valid = [b for b in boards if any(k == b for k, _ in AVAILABLE_BOARDS)]
    valid = valid[:MAX_BOARDS]
    if not valid:
        raise ValueError("at least one valid leaderboard is required")
    settings = dict(await db.get_guild_settings(guild_id) or {})
    settings["leaderboards"] = valid
    await db.set_guild_settings(guild_id, settings)
    return valid


def board_label(board_key: str) -> str:
    """Human label for a board key."""
    for key, label in AVAILABLE_BOARDS:
        if key == board_key:
            return label
    return board_key
