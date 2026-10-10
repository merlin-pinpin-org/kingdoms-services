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
    services = _build_services(config)
    return services[0] if services is not None else None


def _build_services(config: Any) -> tuple[Any, ...] | None:
    """Build the full service ecosystem (Mongo-backed); None when unwired.

    KingdomsService plus the territory/attack/economy/diplomacy
    services the market panel consumes (reference §20, D9/D15/D36/
    D47/D48/D60/D68/D74): they share the same store and season
    config, so they are built once, together.
    """
    if not getattr(config, "mongo_uri", ""):
        return None
    try:
        from kingdoms.core.models.db import get_async_database
        from kingdoms.mods.kingdoms.attacks import AttackService
        from kingdoms.mods.kingdoms.config import load_season_config
        from kingdoms.mods.kingdoms.diplomacy import DiplomacyService
        from kingdoms.mods.kingdoms.economy import EconomyService
        from kingdoms.mods.kingdoms.service import KingdomsService
        from kingdoms.mods.kingdoms.storage import MongoKingdomsStore
        from kingdoms.mods.kingdoms.territories import TerritoryService

        season_config = load_season_config(config.config_dir)
        store = MongoKingdomsStore(get_async_database())
        kingdoms = KingdomsService(store, season_config)
        territories = TerritoryService(store, season_config, kingdoms)
        attacks = AttackService(store, season_config, kingdoms, territories)
        economy = EconomyService(store, season_config, kingdoms, territories, attacks)
        diplomacy = DiplomacyService(store, season_config, kingdoms, territories)
        return kingdoms, territories, attacks, economy, diplomacy
    except Exception:
        logger.exception("kingdoms mod: service build failed \u2014 mod degrades to read-only")
        return None


def register(bot: Any, config: Any) -> None:
    """Wire the mod onto the bot: service, commands, panel wiring."""
    from kingdoms.mods.kingdoms.kingdom_persistent import (
        register_kingdoms_dm_listener,
        register_kingdoms_panel_bot,
    )
    from kingdoms.mods.kingdoms.kingdom_setup import register_kingdom_command
    from kingdoms.mods.kingdoms.kingdoms import register_kingdoms_command
    from kingdoms.mods.kingdoms.kingdoms_admin import register_kingdoms_admin_command

    services = _build_services(config)
    if services is None:
        bot.kingdoms_service = None
        bot.kingdoms_territories_service = None
        bot.kingdoms_attacks_service = None
        bot.kingdoms_economy_service = None
        bot.kingdoms_diplomacy_service = None
    else:
        kingdoms, territories, attacks, economy, diplomacy = services
        bot.kingdoms_service = kingdoms
        bot.kingdoms_territories_service = territories
        bot.kingdoms_attacks_service = attacks
        bot.kingdoms_economy_service = economy
        bot.kingdoms_diplomacy_service = diplomacy
    status = getattr(bot, "status_service", None)
    bot_admins = tuple(getattr(status, "bot_admins", ()))
    register_kingdoms_panel_bot(bot)
    register_kingdoms_dm_listener(bot)
    register_kingdom_command(bot.tree, bot_admins=bot_admins)
    register_kingdoms_command(bot.tree, bot.kingdoms_service)
    register_kingdoms_admin_command(
        bot.tree,
        bot.kingdoms_service,
        bot_admins,
        roles_service=getattr(bot, "roles_service", None),
    )
    logger.info("kingdoms mod registered (service=%s)", "wired" if services else "unwired")


def setup_hook(bot: Any) -> None:
    """Re-register the persistent Kingdoms components after a restart."""
    from kingdoms.mods.kingdoms.kingdom_persistent import (
        register_kingdoms_dm_listener,
        register_kingdoms_panel_bot,
        register_kingdoms_persistent_items,
    )

    register_kingdoms_panel_bot(bot)
    register_kingdoms_persistent_items(bot)
    register_kingdoms_dm_listener(bot)
    logger.info("kingdoms mod persistent items re-registered")
