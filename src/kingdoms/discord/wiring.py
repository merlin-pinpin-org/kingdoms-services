"""Shared wiring seams: one builder per cross-cutting dependency.

Every Discord surface (games admin, maps forum, pools forum, pool
flow) rebuilds the same wirings — the GameDataService over the shared
Mongo adapter, the admin guard, the guild-category resolution. Those
live here once so the surfaces stay thin (the core/mods rule: the mod
code minimal, the shared machinery in core).
"""

from __future__ import annotations

import logging
import os
from typing import Any

import discord

logger = logging.getLogger("kingdoms.wiring")

_GAMES_SERVICE: Any | None = None
_GAMES_SERVICE_READY = False


def games_wiring_ready() -> bool:
    """Whether the game-data wiring can be built (Mongo configured)."""
    return bool(os.environ.get("MONGO_URI"))


def build_games_service() -> Any | None:
    """Build (and memoize) the GameDataService; None when Mongo is absent."""
    global _GAMES_SERVICE, _GAMES_SERVICE_READY
    if _GAMES_SERVICE_READY:
        return _GAMES_SERVICE
    _GAMES_SERVICE_READY = True
    if not os.environ.get("MONGO_URI"):
        return None
    try:
        from kingdoms.core.games.aoe2.seed import MongoAoE2Database
        from kingdoms.core.models.db import get_async_database
        from kingdoms.core.services.game_data import GameDataService

        _GAMES_SERVICE = GameDataService(MongoAoE2Database(get_async_database()))
    except Exception:
        logger.warning("GAMES WIRING build failed", exc_info=True)
        _GAMES_SERVICE = None
    return _GAMES_SERVICE


async def guard_admin(interaction: discord.Interaction, denied_message: str = "Réservé aux admins.") -> bool:
    """Deny non-admins ephemerally; True when the clicker may proceed."""
    from kingdoms.discord.guards import require_admin

    bot = interaction.client
    admins = getattr(getattr(bot, "status_service", None), "bot_admins", ())
    roles = getattr(bot, "roles_service", None)
    return await require_admin(interaction, admins, roles, denied_message=denied_message)


async def guild_category(guild: discord.Guild, name: str, *, create_reason: str = "") -> Any | None:
    """Resolve a guild category by name, creating it when missing.

    The first creation labels the category; afterwards the surfaces
    keep passing the same id — for id-based persistence they store the
    resolved id (never the name) in the channels registry.
    """
    category = discord.utils.get(guild.categories, name=name)
    if category is None:
        try:
            category = await guild.create_category(
                name, reason=create_reason or f"kingdoms: {name} category"
            )
        except Exception:
            logger.warning("category creation failed (%s) — best-effort", name, exc_info=True)
            return None
    return category
