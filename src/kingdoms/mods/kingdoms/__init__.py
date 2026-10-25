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

# On-demand rebuild bookkeeping: a failed self-heal attempt is logged
# once per bot (ERROR, with traceback) instead of once per click.
_ENSURE_FAILED: dict[str, bool] = {}


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
        from kingdoms.mods.kingdoms.admin_debug import DebugService
        from kingdoms.mods.kingdoms.attacks import AttackService
        from kingdoms.mods.kingdoms.cathedrale import CathedralService
        from kingdoms.mods.kingdoms.config import load_season_config
        from kingdoms.mods.kingdoms.diplomacy import DiplomacyService
        from kingdoms.mods.kingdoms.distributor import DistributorService
        from kingdoms.mods.kingdoms.economy import EconomyService
        from kingdoms.mods.kingdoms.events import EventService
        from kingdoms.mods.kingdoms.ffa_exploration import FFAExplorationService
        from kingdoms.mods.kingdoms.mercenary import MercenaryService
        from kingdoms.mods.kingdoms.service import KingdomsService
        from kingdoms.mods.kingdoms.storage import MongoKingdomsStore
        from kingdoms.mods.kingdoms.territories import TerritoryService
        from kingdoms.mods.kingdoms.tavern import TavernService
        from kingdoms.mods.kingdoms.time_machine import TimeMachineService
        from kingdoms.mods.kingdoms.voliere import VoliereService

        season_config = load_season_config(config.config_dir)
        store = MongoKingdomsStore(get_async_database())
        kingdoms = KingdomsService(store, season_config)
        territories = TerritoryService(store, season_config, kingdoms)
        attacks = AttackService(store, season_config, kingdoms, territories)
        economy = EconomyService(store, season_config, kingdoms, territories, attacks)
        diplomacy = DiplomacyService(store, season_config, kingdoms, territories)
        events = EventService(store, season_config, kingdoms, territories, attacks, diplomacy)
        time_machine = TimeMachineService(store, season_config, kingdoms, events)
        distributor = DistributorService(store, season_config, kingdoms, territories, attacks)
        voliere = VoliereService(store, season_config, kingdoms, territories, attacks)
        tavern = TavernService(store, season_config, kingdoms)
        mercenary = MercenaryService(store, season_config, kingdoms)
        ffa = FFAExplorationService(store, season_config, kingdoms, events)
        cathedrals = CathedralService(store, season_config, kingdoms, territories)
        debug = DebugService(
            store,
            season_config,
            kingdoms,
            territories,
            attacks,
            voliere,
            time_machine=time_machine,
        )
        return (
            kingdoms,
            territories,
            attacks,
            economy,
            diplomacy,
            events,
            time_machine,
            distributor,
            voliere,
            tavern,
            mercenary,
            ffa,
            cathedrals,
            debug,
        )
    except Exception:
        logger.exception("kingdoms mod: service build failed \u2014 mod degrades to read-only")
        return None


def register(bot: Any, config: Any) -> None:
    """Wire the mod onto the bot: service, commands, panel wiring."""
    from kingdoms.mods.kingdoms.kingdom_persistent import (
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
        bot.kingdoms_events_service = None
        bot.kingdoms_time_service = None
        bot.kingdoms_distributor_service = None
        bot.kingdoms_voliere_service = None
        bot.kingdoms_tavern_service = None
        bot.kingdoms_mercenary_service = None
        bot.kingdoms_ffa_service = None
        bot.kingdoms_cathedral_service = None
        bot.kingdoms_debug_service = None
    else:
        (
            kingdoms,
            territories,
            attacks,
            economy,
            diplomacy,
            events,
            time_machine,
            distributor,
            voliere,
            tavern,
            mercenary,
            ffa,
            cathedrals,
            debug,
        ) = services
        bot.kingdoms_service = kingdoms
        bot.kingdoms_territories_service = territories
        bot.kingdoms_attacks_service = attacks
        bot.kingdoms_economy_service = economy
        bot.kingdoms_diplomacy_service = diplomacy
        bot.kingdoms_events_service = events
        bot.kingdoms_time_service = time_machine
        bot.kingdoms_distributor_service = distributor
        bot.kingdoms_voliere_service = voliere
        bot.kingdoms_tavern_service = tavern
        bot.kingdoms_mercenary_service = mercenary
        bot.kingdoms_ffa_service = ffa
        bot.kingdoms_cathedral_service = cathedrals
        bot.kingdoms_debug_service = debug
    status = getattr(bot, "status_service", None)
    bot_admins = tuple(getattr(status, "bot_admins", ()))
    register_kingdoms_panel_bot(bot)
    register_kingdom_command(bot.tree, bot_admins=bot_admins)
    # admin-console-v1: the 🛠 console-admin listener (replaces the
    # /kingdoms-admin debug slash subgroup — sync silent failures).
    from kingdoms.mods.kingdoms.admin_console import register_kingdoms_admin_console

    register_kingdoms_admin_console(bot)
    register_kingdoms_command(bot.tree, bot.kingdoms_service)
    register_kingdoms_admin_command(
        bot.tree,
        bot.kingdoms_service,
        bot_admins,
        roles_service=getattr(bot, "roles_service", None),
        debug_service=getattr(bot, "kingdoms_debug_service", None),
    )
    if services is None:
        # 2026-10-24 live incident (Drasah): an unwired boot degraded
        # silently — panels and buttons worked, but every enrollment,
        # king DM and console read no-opped without a single visible
        # signal. An unwired registration is an incident, not a mode.
        logger.error(
            "kingdoms mod registered (service=unwired) — enrollments, king DMs"
            " and the admin console are DEAD until the service rebuilds"
            " (first use retries, see ensure_kingdoms_services)"
        )
    else:
        logger.info("kingdoms mod registered (service=wired)")


def ensure_kingdoms_services(bot: Any) -> Any | None:
    """Return the bot's KingdomsService, rebuilding the stack when missing.

    Drasah's 2026-10-24 live incident: when the Mongo service stack fails
    to build at boot the mod registers unwired — the persistent views and
    channels still work, so a candidature approval "succeeds" (role
    assigned) while the enrollment and the King kingdom-request DM
    silently no-op. Instead of staying dead for the whole process
    lifetime, the first need re-attempts the build from the environment
    (the Mongo clients are lazy — the attempt is cheap and pure).

    Returns the KingdomsService, or None when the rebuild fails too
    (logged ONCE per bot at ERROR with the traceback — the operator gets
    the real root cause instead of a silent degradation).
    """
    if getattr(bot, "kingdoms_service", None) is not None:
        return bot.kingdoms_service
    key = str(id(bot))
    if _ENSURE_FAILED.get(key):
        return None
    try:
        from kingdoms.discord.bot.factory import BotConfig

        services = _build_services(BotConfig.from_env())
    except Exception:
        _ENSURE_FAILED[key] = True
        logger.exception("kingdoms mod: on-demand service rebuild failed — mod stays unwired")
        return None
    if services is None:
        _ENSURE_FAILED[key] = True
        logger.error(
            "kingdoms mod: on-demand service rebuild returned no services"
            " (mongo_uri absent?) — mod stays unwired"
        )
        return None
    (
        kingdoms,
        territories,
        attacks,
        economy,
        diplomacy,
        events,
        time_machine,
        distributor,
        voliere,
        tavern,
        mercenary,
        ffa,
        cathedrals,
        debug,
    ) = services
    bot.kingdoms_service = kingdoms
    bot.kingdoms_territories_service = territories
    bot.kingdoms_attacks_service = attacks
    bot.kingdoms_economy_service = economy
    bot.kingdoms_diplomacy_service = diplomacy
    bot.kingdoms_events_service = events
    bot.kingdoms_time_service = time_machine
    bot.kingdoms_distributor_service = distributor
    bot.kingdoms_voliere_service = voliere
    bot.kingdoms_tavern_service = tavern
    bot.kingdoms_mercenary_service = mercenary
    bot.kingdoms_ffa_service = ffa
    bot.kingdoms_cathedral_service = cathedrals
    bot.kingdoms_debug_service = debug
    logger.error(
        "kingdoms mod: service stack REBUILT on demand (the boot was unwired)"
        " — enrollments, king DMs and the console are back"
    )
    return kingdoms


def setup_hook(bot: Any) -> None:
    """Re-register the persistent Kingdoms components after a restart."""
    from kingdoms.mods.kingdoms.kingdom_persistent import (
        register_kingdoms_panel_bot,
        register_kingdoms_persistent_items,
    )

    register_kingdoms_panel_bot(bot)
    register_kingdoms_persistent_items(bot)
    from kingdoms.mods.kingdoms.kingdom_events_loop import start_kingdoms_event_scheduler

    started = start_kingdoms_event_scheduler(bot)
    logger.info(
        "kingdoms mod persistent items re-registered (scheduler=%s)",
        "started" if started else "skipped",
    )
