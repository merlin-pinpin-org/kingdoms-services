"""Kingdoms mod: the AoE2 territory-conquest Season II feature.

The mod is **game-bound** (AoE2 only, by design — CADASTRE.md and the
civilization rules are AoE2 data): it reuses the core's game catalog
(maps, map pools, the guild's ``aoe2-maps``/``aoe2-map-pools`` forum
topics) and the core's season registry, never its own.

Season ids follow the core's visible-id convention with the mod's own
scope: ``kingdoms-aoe2-<guild_id>-<index>`` — the index is incremental
per guild, uniqueness is the (scope, index) pair.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("kingdoms.mods.kingdoms")

GAME_KEY = "aoe2"


def season_scope(guild_id: str) -> str:
    """Build the core season scope for this mod's seasons in one guild."""
    return f"kingdoms-{GAME_KEY}-{guild_id}"


def _season_yaml(config: Any) -> Path:
    """Resolve the mod's season.yaml from the bot's config directory."""
    return Path(getattr(config, "config_dir", "config")) / "kingdoms" / "season.yaml"


async def graft_core_catalog(config: Any, game_data: Any) -> Any:
    """Graft the core's map catalog onto the mod's season config.

    The core's ``maps`` collection (fed by the seed and the games admin
    panels, surfaced as the guild's ``aoe2-maps``/``aoe2-map-pools``
    forum topics) is the single source of truth for AoE2 maps; the
    mod's hard catalog stays the fallback when the core collection is
    empty (fresh env, unit tests).
    """
    from kingdoms.mods.kingdoms.config import core_catalog_to_entries

    try:
        maps = await game_data.list_maps(GAME_KEY)
    except Exception:
        logger.warning("KINGDOMS mod: core map catalog unavailable — hard fallback", exc_info=True)
        return config
    if maps:
        entries = core_catalog_to_entries(maps)
        if entries:
            return config.model_copy(update={"maps": entries})
    return config


def register(bot: Any, config: Any) -> None:
    """Wire the Kingdoms mod onto the bot: store, services, commands, panels.

    The map-catalog graft happens at season launch (async context):
    ``KingdomsService.launch`` reads the core catalog through the
    ``game_data`` seam, so the drawn territories always match what the
    guild sees in its maps forum.
    """
    from kingdoms.mods.kingdoms.config import load_season_config
    from kingdoms.mods.kingdoms.service import KingdomsService
    from kingdoms.mods.kingdoms.storage import MongoKingdomsStore

    mongo_uri = os.environ.get("MONGO_URI", "")
    if not mongo_uri:
        logger.info("KINGDOMS mod: Mongo not configured — mod inactive")
        return
    from kingdoms.core.games.aoe2.seed import MongoAoE2Database
    from kingdoms.core.models.db import get_async_database
    from kingdoms.core.services.game_data import GameDataService
    from kingdoms.core.services.season import SeasonService

    database = get_async_database()
    store = MongoKingdomsStore(database)
    season_config = load_season_config(_season_yaml(config))
    adapter = MongoAoE2Database(database)
    game_data = GameDataService(adapter)
    core_seasons = SeasonService(adapter, game_data)
    service = KingdomsService(
        store,
        season_config,
        core_seasons=core_seasons,
        guild_id=(getattr(config, "sync_guild_id", "") or "").strip(),
        game_data=game_data,
    )
    bot.kingdoms_service = service

    from kingdoms.mods.kingdoms.territories import TerritoryService

    bot.territories_service = TerritoryService(store, season_config, service)

    status = getattr(bot, "status_service", None)
    from kingdoms.mods.kingdoms.kingdoms import register_kingdoms_command
    from kingdoms.mods.kingdoms.kingdoms_admin import register_kingdoms_admin_command

    register_kingdoms_command(bot.tree, service)
    register_kingdoms_admin_command(
        bot.tree,
        service,
        bot_admins=tuple(getattr(status, "bot_admins", ())),
        roles_service=getattr(bot, "roles_service", None),
    )
    from kingdoms.mods.kingdoms.kingdom_persistent import register_kingdoms_panel_bot

    register_kingdoms_panel_bot(bot)


def setup_hook(bot: Any) -> None:
    """Re-register the Kingdoms persistent UI after a restart."""
    from kingdoms.mods.kingdoms.kingdom_persistent import register_kingdoms_persistent_items

    register_kingdoms_persistent_items(bot)


def close(bot: Any) -> None:
    """Stop the mod's background tasks (none today; the hook is the contract)."""
    del bot
