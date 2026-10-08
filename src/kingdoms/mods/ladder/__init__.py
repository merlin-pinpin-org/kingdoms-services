"""Ladder mod entrypoint: the only surface the core calls.

The core discovers this package through the mod entrypoint seam
(:mod:`kingdoms.core.services.mod_entrypoint`) — for the enabled
``ladder`` mod it imports ``kingdoms.mods.ladder`` and calls these
hooks. Everything ladder-specific (commands wiring, home view, admin
panel section, channels sync, season roles) is registered here; the
core's factory contains no ladder reference.

Hooks degrade quietly: without Mongo/Redis the wiring builders return
None and the mod stays inactive, exactly like the core services.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from kingdoms.mods.ladder.ladder_ids import ladder_id

logger = logging.getLogger("kingdoms.mods.ladder")


def _guild_id(config: Any) -> str:
    """Guild id of the default ladder (the bot's sync guild, test envs only)."""
    return (getattr(config, "sync_guild_id", "") or "").strip()


def register(bot: Any, config: Any) -> None:
    """Wire the ladder onto the bot: roles, commands, home, admin panel."""
    from kingdoms.core.services.season_roles import SeasonRolesService

    if getattr(bot, "mod_roles_service", None) is not None:
        try:
            bot.season_roles_service = SeasonRolesService(bot.mod_roles_service, "ladder")
        except Exception:
            logger.exception("LADDER season roles wiring failed — per-season roles degrade")

    from .commands import build_ladder_wiring, register_ladder_commands, start_ladder_sweep

    wiring = build_ladder_wiring(bot=bot, season_roles=getattr(bot, "season_roles_service", None))
    if wiring is None:
        logger.info("LADDER wiring unavailable (Mongo/Redis) — mod inactive")
        return
    register_ladder_commands(bot.tree, wiring, owner_ref=_guild_id(config))
    bot.season_service = wiring.season_service
    bot._ladder_id = ladder_id("aoe2", _guild_id(config))
    bot._ladder_sweep_task = start_ladder_sweep(wiring)

    from .admin_panel import register_ladder_admin_items, register_ladder_admin_section

    register_ladder_admin_section()
    register_ladder_admin_items(bot)

    from .home import build_ladder_home_view, register_ladder_home_items

    bot.mod_home_builders["ladder"] = build_ladder_home_view
    bot.mod_profile_enrichers["ladder"] = _profile_enricher
    register_ladder_home_items(bot)


async def _profile_enricher(bot: Any) -> dict[str, dict[str, Any]]:
    """Map discord user id -> ladder player doc (core profile seam)."""
    from .commands import build_ladder_wiring

    wiring = build_ladder_wiring()
    ladder_id = str(getattr(bot, "_ladder_id", "") or "")
    if wiring is None or not ladder_id:
        return {}
    try:
        players = await wiring.service._db.find_ladder_players(ladder_id)
        return {str(p.get("user_id", "")): p for p in players}
    except Exception:
        return {}


def setup_hook(bot: Any) -> None:
    """Re-register the ladder's persistent UI after a restart."""
    from .admin_channel import (
        build_ladder_admin_channel_service,
        maintain_pinned_ladder_admin_menus,
        register_ladder_mod_admin_channel,
    )
    from .channels import ladder_channels_wiring_ready, start_ladder_channels_sync

    register_ladder_mod_admin_channel(bot)
    bot.ladder_admin_channel_service = build_ladder_admin_channel_service(
        bot, bot.config.mongo_uri, bot.config.redis_uri
    )
    if bot.ladder_admin_channel_service is not None:
        bot._ladder_admin_pin_task = asyncio.create_task(maintain_pinned_ladder_admin_menus(bot))
    if ladder_channels_wiring_ready():
        bot._ladder_channels_task = start_ladder_channels_sync(bot)
    bot._ladder_season_roles_task = asyncio.create_task(_sweep_season_roles(bot))


def close(bot: Any) -> None:
    """Cancel the ladder's background tasks."""
    for attr in (
        "_ladder_sweep_task",
        "_ladder_admin_pin_task",
        "_ladder_channels_task",
        "_ladder_season_roles_task",
    ):
        task = getattr(bot, attr, None)
        if task is not None:
            task.cancel()


async def _sweep_season_roles(bot: Any) -> None:
    """Grant the active season's player role to every enrolled player.

    The season import (CLI, botless) writes ``season_enrollments``; the
    Discord role only exists bot-side, so this startup sweep is the
    import's role step: idempotent (assigning an already-held role is a
    no-op) and quiet on failure — a role outage never blocks the bot.
    """
    from kingdoms.core.services.season_roles import season_label

    await asyncio.sleep(20)
    while True:
        try:
            from .commands import build_ladder_wiring

            wiring = build_ladder_wiring()
            season_roles = getattr(bot, "season_roles_service", None)
            guild_id = (getattr(getattr(bot, "config", None), "sync_guild_id", "") or "").strip()
            if wiring is None or season_roles is None or not guild_id.isdigit():
                return
            database = wiring.database
            guild = bot.get_guild(int(guild_id))
            if guild is None:
                return
            async for season in database["seasons"].find({"state": "active"}):
                season_id = str(season.get("_id", ""))
                if not season_id:
                    continue
                label = season_label(season)
                async for enrollment in database["season_enrollments"].find({"season_id": season_id}):
                    user_id = str(enrollment.get("user_id", ""))
                    member = guild.get_member(int(user_id)) if user_id.isdigit() else None
                    if member is None:
                        continue
                    await season_roles.sync_player_role(guild_id, user_id, label, member=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("LADDER season-role sweep failed — best-effort", exc_info=True)
            return
