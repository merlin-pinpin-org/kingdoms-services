"""Kingdoms mod: the AoE2 territory-conquest Season II feature.

Lifecycle hooks (the mod_entrypoint contract): ``register`` wires the
mod onto the bot at build time (services, commands, panel wiring) and
``setup_hook`` re-registers the persistent UI at every startup \u2014 the
restart-proof state reconstruction (\u00a73b). Without these hooks the mod
loads but nothing reaches Discord: no /kingdom, no /kingdoms screens,
dead buttons.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("kingdoms.mods.kingdoms")

MOD_NAME = "kingdoms"


def _build_service(config: Any) -> Any | None:
    """Build the KingdomsService (Mongo-backed); None when unwired."""
    if not getattr(config, "mongo_uri", ""):
        return None
    try:
        from kingdoms.core.models.db import get_async_database
        from kingdoms.mods.kingdoms.config import load_season_config
        from kingdoms.mods.kingdoms.service import KingdomsService
        from kingdoms.mods.kingdoms.storage import MongoKingdomsStore

        season_config = load_season_config(config.config_dir)
        return KingdomsService(MongoKingdomsStore(get_async_database()), season_config)
    except Exception:
        logger.exception("kingdoms mod: service build failed \u2014 mod degrades to read-only")
        return None


def register(bot: Any, config: Any) -> None:
    """Wire the mod onto the bot: service, commands, panel wiring."""
    from kingdoms.mods.kingdoms.kingdom_persistent import register_kingdoms_panel_bot
    from kingdoms.mods.kingdoms.kingdom_setup import register_kingdom_command
    from kingdoms.mods.kingdoms.kingdoms import register_kingdoms_command
    from kingdoms.mods.kingdoms.kingdoms_admin import register_kingdoms_admin_command

    service = _build_service(config)
    bot.kingdoms_service = service
    status = getattr(bot, "status_service", None)
    bot_admins = tuple(getattr(status, "bot_admins", ()))
    register_kingdoms_panel_bot(bot)
    register_kingdom_command(bot.tree, bot_admins=bot_admins)
    register_kingdoms_command(bot.tree, service)
    register_kingdoms_admin_command(
        bot.tree,
        service,
        bot_admins,
        roles_service=getattr(bot, "roles_service", None),
    )
    logger.info("kingdoms mod registered (service=%s)", "wired" if service else "unwired")


def setup_hook(bot: Any) -> None:
    """Re-register the persistent Kingdoms components after a restart."""
    from kingdoms.mods.kingdoms.kingdom_persistent import (
        register_kingdoms_panel_bot,
        register_kingdoms_persistent_items,
    )

    register_kingdoms_panel_bot(bot)
    register_kingdoms_persistent_items(bot)
    logger.info("kingdoms mod persistent items re-registered")
