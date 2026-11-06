r"""Headless install runner: the console's install action without a bot.

The Discord ``/console`` install modal and this runner are the same
action (replace the grants, clean the uninstalled mods' channels and
roles, provision the installed ones through the standard seams). The
modal rides the running bot's wirings; this runner rebuilds them from a
bare ``discord.Client`` \\u2014 the CLI (kingdoms.cli.console_ops) and the
env-console workflow use it on the environment's kingdoms-core
container, where the code and the Mongo network are both available
(same execution model as the seed CLIs).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import discord

logger = logging.getLogger("kingdoms.discord.console_ops_runner")

__all__ = [
    "apply_install_for_guild",
    "build_console_wirings",
]

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


def build_console_wirings(client: discord.Client) -> dict[str, Any] | None:
    """Rebuild the console's wirings from a bare client; None unwired."""
    import os

    if not os.environ.get("MONGO_URI") or not os.environ.get("REDIS_URI"):
        logger.error("CONSOLE OPS wiring needs MONGO_URI and REDIS_URI")
        return None
    try:
        from kingdoms.core.models.db import get_async_database
        from kingdoms.core.services.channel import ChannelService
        from kingdoms.core.services.mod_registry import ModRegistry, load_mod_definitions
        from kingdoms.core.services.roles import ModRolesService
        from kingdoms.core.services.state import StateService
        from kingdoms.discord.channels_platform import DiscordChannelsPlatform
        from kingdoms.discord.console import _propagate
        from kingdoms.discord.logs_platform import MongoLogsDatabase
        from kingdoms.discord.roles_platform import DiscordRolesPlatform, MongoRolesDatabase

        registry = ModRegistry(load_mod_definitions(CONFIG_DIR))
        database = get_async_database()
        state = StateService(redis_uri=os.environ["REDIS_URI"])
        channel_service = ChannelService(
            database=MongoLogsDatabase(database),
            platform=DiscordChannelsPlatform(client),
            cache=state,
            registry=registry,
        )
        mod_roles_service = ModRolesService(
            database=MongoRolesDatabase(database),
            platform=DiscordRolesPlatform(client),
            registry=registry,
            members=DiscordRolesPlatform(client),
            cache=state,
        )
        return {
            "registry": registry,
            "channel_service": channel_service,
            "mod_roles_service": mod_roles_service,
            "propagate": _propagate,
        }
    except Exception:
        logger.exception("CONSOLE OPS wiring build failed")
        return None


async def apply_install_for_guild(
    guild: discord.Guild,
    mods: list[str],
    games: list[str],
    propagate: bool = True,
) -> str:
    """Apply the install selection to one guild (grants + clean + provision)."""
    from kingdoms.core.models.db import get_async_database
    from kingdoms.core.services.guild_access import GuildAccessService
    from kingdoms.core.services.guild_access_mongo import MongoGuildAccessDatabase

    wirings = build_console_wirings(_client_of(guild))
    if wirings is None:
        return "Wiring unavailable (MONGO_URI/REDIS_URI) — grants cannot be applied."
    registry = wirings["registry"]
    if propagate:
        games, mods = wirings["propagate"](registry, _all_games(), _all_mods(registry), mods, games)

    guild_id = str(guild.id)
    access = GuildAccessService(
        MongoGuildAccessDatabase(get_async_database()), games=_all_games(), mods=_all_mods(registry)
    )
    previous_mods = set(await access.enabled_mods(guild_id))
    doc = await access.set_grants(guild_id, games=games, mods=mods)
    removed = previous_mods - set(mods)
    lines = [f"Grants updated: mods {doc['mods']}, games {doc['games']}."]

    channel_service = wirings["channel_service"]
    mod_roles_service = wirings["mod_roles_service"]
    for mod_name in sorted(removed):
        await _uninstall_mod_for_guild(guild, mod_name, channel_service, mod_roles_service)
    if removed:
        lines.append(f"Uninstalled mods cleaned (channels + roles): {sorted(removed)}.")

    provisioned = 0
    for mod_name in mods:
        try:
            await channel_service.setup_mod_channels(guild_id, mod_name)
            await mod_roles_service.setup_mod_roles(guild_id, mod_name)
            provisioned += 1
        except Exception:
            logger.warning("CONSOLE OPS provisioning failed (%s)", mod_name, exc_info=True)
    lines.append(f"{provisioned} mod(s) provisioned.")
    return "\n".join(lines)


def _client_of(guild: discord.Guild) -> discord.Client:
    """Recover the client from a guild object (discord.py state seam)."""
    return guild._state._get_client()


def _all_games() -> tuple[str, ...]:
    """Return the platform's known games (config/games directories)."""
    games_dir = CONFIG_DIR / "games"
    if not games_dir.is_dir():
        return ()
    return tuple(sorted(entry.name for entry in games_dir.iterdir() if entry.is_dir()))


def _all_mods(registry: Any) -> tuple[str, ...]:
    return tuple(registry.enabled())


async def _uninstall_mod_for_guild(
    guild: discord.Guild,
    mod_name: str,
    channel_service: Any,
    mod_roles_service: Any,
) -> None:
    """Delete one uninstalled mod's channels and roles, then their mappings."""
    registry_mods = channel_service._registry
    definition = registry_mods.get(mod_name) if registry_mods is not None else None
    guild_id = str(guild.id)
    for category_def in getattr(definition, "channel_categories", ()) or ():
        category = f"{mod_name}:{category_def.key}"
        stored = await channel_service._db.find_channel(guild_id, category)
        if stored is not None:
            channel = guild.get_channel(int(stored.channel_id))
            if channel is not None:
                try:
                    await channel.delete(reason=f"kingdoms console ops: uninstall {mod_name}")
                except Exception:
                    logger.warning("CONSOLE OPS uninstall channel failed (%s)", category, exc_info=True)
        await channel_service._db.delete_channel(guild_id, category)
        await channel_service._cache.delete_state("channels", f"{guild_id}:{category}")
    for mapping in await mod_roles_service._db.list_role_mappings(guild_id, mod_name):
        role = guild.get_role(int(mapping.role_id))
        if role is not None:
            try:
                await role.delete(reason=f"kingdoms console ops: uninstall {mod_name}")
            except Exception:
                logger.warning("CONSOLE OPS uninstall role failed (%s)", mapping.role_key, exc_info=True)
        await mod_roles_service._db.delete_role_mapping(guild_id, mod_name, mapping.role_key)
        await mod_roles_service._cache.delete_state("roles:mod", f"{guild_id}:{mod_name}:{mapping.role_key}")
