"""The query indexes of the live dashboards' hot collections.

The hot paths (owner direction: the players panel must be immediate):
``collect_profile_stats`` reads the profile bindings by
``(game_key, profile_id)`` and aggregates the completed matches by
profile side; the profile lookups join the bindings by user.

Every index is idempotent (Mongo's ``create_index`` is a no-op when
the index exists) — safe to call from every boot.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("kingdoms.core.db_indexes")


async def ensure_live_indexes(database: Any) -> None:
    """Create the hot-path indexes (idempotent, best-effort)."""
    try:
        # profile_bindings: the panel's batched read
        # (game_key, profile_id) — the find($in) rides the pair.
        await database["profile_bindings"].create_index(
            [("game_key", 1), ("profile_id", 1)]
        )
        # profile_bindings: the per-user roster joins (list_bindings).
        await database["profile_bindings"].create_index(
            [("game_key", 1), ("user_id", 1)]
        )
        # The GAME collections' indexes moved to the game service
        # (rule 5: every collection has ONE owner — matches and
        # player_state belong to game:aoe2, ensured at ITS boot).
        logger.info("live indexes ensured")
    except Exception:
        logger.warning("live index creation failed — best-effort", exc_info=True)
