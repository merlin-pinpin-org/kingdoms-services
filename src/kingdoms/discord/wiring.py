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


_GUILD_ACCESS_SERVICE: Any | None = None
_GUILD_ACCESS_READY = False
_GUILD_ACCESS_PLATFORM: tuple[str, ...] = ()
_GUILD_ACCESS_MODS: tuple[str, ...] = ()


def set_guild_access_platform(games: tuple[str, ...], mods: tuple[str, ...]) -> None:
    """Record the platform's known games and mods (bot build time)."""
    global _GUILD_ACCESS_PLATFORM, _GUILD_ACCESS_MODS
    _GUILD_ACCESS_PLATFORM = tuple(games)
    _GUILD_ACCESS_MODS = tuple(mods)


def guild_access_platform() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return the platform's known games and mods (for request pickers)."""
    return _GUILD_ACCESS_PLATFORM, _GUILD_ACCESS_MODS


def build_guild_access_service() -> Any | None:
    """Build (and memoize) the GuildAccessService; None when Mongo is absent."""
    global _GUILD_ACCESS_SERVICE, _GUILD_ACCESS_READY
    if _GUILD_ACCESS_READY:
        return _GUILD_ACCESS_SERVICE
    _GUILD_ACCESS_READY = True
    if not os.environ.get("MONGO_URI"):
        return None
    try:
        from kingdoms.core.models.db import get_async_database
        from kingdoms.core.services.guild_access import GuildAccessService
        from kingdoms.core.services.guild_access_mongo import MongoGuildAccessDatabase

        _GUILD_ACCESS_SERVICE = GuildAccessService(
            MongoGuildAccessDatabase(get_async_database()),
            games=_GUILD_ACCESS_PLATFORM,
            mods=_GUILD_ACCESS_MODS,
        )
    except Exception:
        logger.warning("GUILD ACCESS wiring build failed", exc_info=True)
        return None
    return _GUILD_ACCESS_SERVICE


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
            category = await guild.create_category(name, reason=create_reason or f"kingdoms: {name} category")
        except Exception:
            logger.warning("category creation failed (%s) — best-effort", name, exc_info=True)
            return None
    return category


_PROVIDER_MAPPING_SERVICE: Any | None = None
_PROVIDER_MAPPING_READY = False


def build_provider_mapping_service() -> Any | None:
    """Build (and memoize) the ProviderMappingService; None without Mongo."""
    global _PROVIDER_MAPPING_SERVICE, _PROVIDER_MAPPING_READY
    if _PROVIDER_MAPPING_READY:
        return _PROVIDER_MAPPING_SERVICE
    _PROVIDER_MAPPING_READY = True
    if not os.environ.get("MONGO_URI"):
        return None
    try:
        from kingdoms.core.models.db import get_async_database
        from kingdoms.core.services.provider_mapping import ProviderMappingService
        from kingdoms.core.services.provider_mapping_mongo import MongoProviderMappingDatabase

        _PROVIDER_MAPPING_SERVICE = ProviderMappingService(MongoProviderMappingDatabase(get_async_database()))
    except Exception:
        logger.warning("PROVIDER MAPPING wiring build failed", exc_info=True)
        return None
    return _PROVIDER_MAPPING_SERVICE


async def granted_game_keys(guild_id: str, catalog_keys: tuple[str, ...] = ()) -> tuple[str, ...]:
    """List the game keys a guild may provision (access-first).

    The granted keys drive the channel provisioning: forums are created
    on the grant even when the catalog is still empty, and fill up as
    the content arrives. When the access service is unwired the
    catalog's keys stand in (the open-degradation seam).
    """
    service = build_guild_access_service()
    if service is None:
        return catalog_keys
    try:
        granted = await service.enabled_games(guild_id)
        return granted if granted else catalog_keys
    except Exception:
        logger.warning("GUILD ACCESS keys read failed (guild %s)", guild_id, exc_info=True)
        return catalog_keys


async def guild_has_game(guild_id: str, game_key: str) -> bool:
    """Whether the guild was granted one game (access seam; True when unwired).

    When the access service is unavailable (no Mongo), the forums keep
    syncing as before — the seam degrades open for the core surfaces,
    while mod provisioning is the strict seam (nothing active).
    """
    service = build_guild_access_service()
    if service is None:
        return True
    try:
        return game_key in await service.enabled_games(guild_id)
    except Exception:
        logger.warning("GUILD ACCESS game check failed (guild %s)", guild_id, exc_info=True)
        return False
