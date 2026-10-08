"""Ladder-owned seeding: the ``ladders``/``seasons`` sections of the AoE2 seed YAML.

The core ``seed_aoe2`` owns the game catalog (maps, civs, rules, pools);
this module owns everything the ladder mod persists. The core calls it
through the injected ``ladder_seeder`` hook (see ``seed_aoe2``).
"""

from __future__ import annotations

from typing import Any

from kingdoms.core.services.game_data import GameDataService
from kingdoms.core.services.season import SeasonService
from kingdoms.mods.ladder.ladder_ids import ladder_id as ladder_id_for
from kingdoms.mods.ladder.service import LadderService

DAY_MS = 86_400_000


async def seed_ladders(
    adapter: Any,
    data: dict[str, Any],
    game_key: str,
    now_ms: int,
    result: dict[str, Any],
) -> None:
    """Idempotently seed the ladders and their seasons from the YAML document."""
    game_data = GameDataService(adapter)
    ladder_service = LadderService(adapter, game_data)
    season_service = SeasonService(adapter, game_data)
    for spec in data.get("ladders", []) or []:
        owner_ref = spec["owner_ref"]
        ladder_id = ladder_id_for(game_key, owner_ref)
        existing = await ladder_service.get_ladder(ladder_id)
        if existing is None:
            await ladder_service.create_ladder(owner_ref, spec["name"], game_key, now=now_ms)
            result["ladders"] += 1
        pool_name = spec.get("map_pool")
        if pool_name:
            pool_id = f"map_pool:{game_key}:{pool_name}"
            ladder = await ladder_service.get_ladder(ladder_id)
            if ladder is not None and ladder.active_map_pool_id is None:
                await ladder_service.set_active_pool(ladder_id, pool_id)
        season_spec = spec.get("season")
        if season_spec:
            season_name = season_spec["name"]
            existing_seasons = await season_service.list_seasons(ladder_id)
            if not existing_seasons:
                start = now_ms + int(season_spec.get("start_in_days", 0)) * DAY_MS
                duration = int(season_spec.get("duration_days", 0)) or None
                end = start + duration * DAY_MS if duration else None
                await season_service.create_season(
                    ladder_id,
                    season_name,
                    f"map_pool:{game_key}:{season_spec.get('map_pool', spec.get('map_pool', pool_name))}",
                    start,
                    end_at=end,
                    reset_ratings=bool(season_spec.get("reset_ratings", False)),
                )
                result["seasons"] += 1
