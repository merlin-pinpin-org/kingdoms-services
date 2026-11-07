"""The game's own collection indexes (rule 5: one owner per collection).

``matches`` and ``player_state`` belong to game:aoe2 — the game service
ensures their indexes at ITS boot (idempotent, best-effort), never the
core nor the bot.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("kingdoms.game_aoe2.db_indexes")


async def ensure_game_indexes(database: Any) -> None:
    """Create the game collections' hot-path indexes (idempotent)."""
    try:
        # matches: the last-completed aggregation filters by profile
        # side first — one index per side, the status filtered after.
        await database["matches"].create_index([("host_profile_id", 1)])
        await database["matches"].create_index([("guest_profile_id", 1)])
        await database["matches"].create_index([("status", 1), ("completed_at", -1)])
        # player_state: the subscriptions-maintained live truth — the
        # panels' snapshot reads ride the profile id.
        await database["player_state"].create_index([("profile_id", 1)], unique=True)
        logger.info("game indexes ensured")
    except Exception:
        logger.warning("game index creation failed — best-effort", exc_info=True)
