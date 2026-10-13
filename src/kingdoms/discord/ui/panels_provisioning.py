"""Panels provisioning: apply the channel-tree rules to a guild.

The declarative counterpart of the SDK: the scopes registered at
boot (games, mods, their games/mods) are provisioned here —
categories with slash names, channels in the canonical order,
guild-locale naming, read-only menu channels, admin channels for
the bot-admins. Idempotent and heal-safe: the resolution matches
channels by their stable registry keys (never by the translated
displayed name), so a rename heals to the right channel.

The heavy lifting (create, overwrites, position) reuses the
existing channels platform — this module only owns the tree rules.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import discord

from kingdoms.discord.ui.panels import ChannelDecl, ScopeSpec, registered_scopes

logger = logging.getLogger("kingdoms.discord.panels_provisioning")

_POSITION_BANDS = {
    "general": 0,
    "discussions": 1,
    "data": 2,
    "admin": 3,
    "decisions": 4,
}


def _band(decl: ChannelDecl) -> int:
    """Return the canonical position band of one channel declaration."""
    if decl.key == "general":
        return _POSITION_BANDS["general"]
    if decl.key == "discussions":
        return _POSITION_BANDS["discussions"]
    if decl.key == "admin":
        return _POSITION_BANDS["admin"]
    if decl.key == "decisions":
        return _POSITION_BANDS["decisions"]
    return _POSITION_BANDS["data"]


def _sorted_channels(spec: ScopeSpec) -> tuple[ChannelDecl, ...]:
    """Sort a scope's channels into the canonical order."""
    return tuple(sorted(spec.channels, key=lambda c: (_band(c), c.position or 0, c.key)))


class _Platform:
    """Thin idempotent create-if-missing over the raw guild."""

    def __init__(self, guild: discord.Guild) -> None:
        self._guild = guild

    def _category(self, category_id: str) -> discord.CategoryChannel | None:
        """Return the category channel (typed), None when absent."""
        if not category_id.isdigit():
            return None
        channel = self._guild.get_channel(int(category_id))
        if isinstance(channel, discord.CategoryChannel):
            return channel
        return None

    async def ensure_category(self, name: str) -> str:
        """Return the category id, creating it when missing."""
        category = discord.utils.get(self._guild.categories, name=name)
        if category is not None:
            return str(category.id)
        created = await self._guild.create_category(name, reason="kingdoms: panels provisioning")
        return str(created.id)

    async def ensure_channel(self, name: str, category_id: str) -> str:
        """Return the text channel id, creating it when missing."""
        category = self._category(category_id)
        channel = discord.utils.get(self._guild.text_channels, name=name)
        if channel is not None:
            if category is not None and channel.category_id != category.id:
                try:
                    await channel.edit(category=category, reason="kingdoms: panels provisioning (adopt)")
                except discord.HTTPException:
                    logger.warning(
                        "panels provisioning: moving %s into its folder failed — kept in place",
                        name,
                        exc_info=True,
                    )
            return str(channel.id)
        created = await self._guild.create_text_channel(
            name, category=category, reason="kingdoms: panels provisioning"
        )
        return str(created.id)

    async def ensure_forum(self, name: str, category_id: str) -> str:
        """Return the forum id, creating it when missing."""
        category = self._category(category_id)
        forum = discord.utils.get(self._guild.forums, name=name)
        if forum is not None:
            if category is not None and forum.category_id != category.id:
                try:
                    await forum.edit(category=category, reason="kingdoms: panels provisioning (adopt)")
                except discord.HTTPException:
                    logger.warning(
                        "panels provisioning: moving %s into its folder failed — kept in place",
                        name,
                        exc_info=True,
                    )
            return str(forum.id)
        created = await self._guild.create_forum(
            name, category=category, reason="kingdoms: panels provisioning"
        )
        return str(created.id)


async def _apply_access(guild: discord.Guild, channel_id: str, decl: ChannelDecl) -> None:
    """Apply the channel's access band (overwrites, best-effort)."""
    channel = guild.get_channel(int(channel_id)) if channel_id.isdigit() else None
    if channel is None:
        return
    try:
        if decl.access == "readonly":
            overwrite = discord.PermissionOverwrite(send_messages=False, add_reactions=True)
            await channel.set_permissions(
                guild.default_role, overwrite=overwrite, reason="kingdoms: readonly menu channel"
            )
        elif decl.access == "admin":
            overwrite = discord.PermissionOverwrite(view_channel=False)
            await channel.set_permissions(
                guild.default_role, overwrite=overwrite, reason="kingdoms: admin channel"
            )
    except Exception:
        logger.warning(
            "panels provisioning: access apply failed (%s) — best-effort",
            decl.key,
            exc_info=True,
        )


async def provision_scope(
    guild: discord.Guild,
    spec: ScopeSpec,
    locale: str,
    translate: Callable[[str, str], str],
) -> dict[str, str]:
    """Provision one scope folder; return key -> channel id.

    ``translate`` renders a label key in the guild's locale
    (``MessageCatalog.render``-shaped callable).
    """
    platform = _Platform(guild)
    resolved: dict[str, str] = {}
    category_id = ""
    if spec.category_key:
        category_name = translate(spec.category_key, locale)
        category_id = await platform.ensure_category(category_name)
        resolved[spec.scope] = category_id
    # an empty category_key is the guild root: its channels live uncategorised
    for decl in _sorted_channels(spec):
        name = translate(decl.label_key, locale)
        try:
            if decl.kind == "forum":
                channel_id = await platform.ensure_forum(name, category_id)
            else:
                channel_id = await platform.ensure_channel(name, category_id)
        except discord.HTTPException:
            logger.warning(
                "panels provisioning: channel %s/%s failed — best-effort, skipped",
                spec.scope,
                decl.key,
                exc_info=True,
            )
            continue
        await _apply_access(guild, channel_id, decl)
        resolved[decl.key] = channel_id
    return resolved


async def provision_all(
    guild: discord.Guild, locale: str, translate: Callable[[str, str], str]
) -> None:
    """Provision every registered scope folder (boot pass, best-effort).

    Every resolution is persisted under its stable key — the stored
    channel ids the syncs resolve by (never by name).
    """
    for spec in registered_scopes():
        try:
            resolved = await provision_scope(guild, spec, locale, translate)
            await persist_resolutions(str(guild.id), spec.scope, resolved)
        except Exception:
            logger.warning(
                "panels provisioning failed (scope %s, guild %s) — best-effort",
                spec.scope,
                guild.id,
                exc_info=True,
            )


async def persist_resolutions(guild_id: str, scope: str, resolved: dict[str, str]) -> None:
    """Persist one scope's resolved channel ids under their stable keys.

    ``tree:<scope>:<channel key>`` — the channels registry documents
    them; the syncs and the pins resolve by these stored ids, never by
    the translated name. Best-effort: a failed write logs and moves on
    (the next boot re-resolves).
    """
    try:
        from kingdoms.core.models.db import get_async_database

        collection = get_async_database()["channels"]
        for key, channel_id in resolved.items():
            await collection.replace_one(
                {"_id": f"{guild_id}:tree:{scope}:{key}"},
                {
                    "_id": f"{guild_id}:tree:{scope}:{key}",
                    "guild_id": guild_id,
                    "platform": "discord",
                    "category": f"tree:{scope}:{key}",
                    "channel_id": channel_id,
                    "name": key,
                },
                upsert=True,
            )
    except Exception:
        logger.warning(
            "panels provisioning: resolution persist failed (scope %s, guild %s) — best-effort",
            scope,
            guild_id,
            exc_info=True,
        )


async def stored_channel_id(guild_id: str, scope: str, channel_key: str) -> str | None:
    """Resolve one tree channel by its stored id (never by name).

    The syncs' resolution contract: the provisioning stored the id at
    creation time; a miss (virgin guild, unwired Mongo) returns None
    and the caller falls back to the provisioning.
    """
    try:
        from kingdoms.core.models.db import get_async_database

        document = await get_async_database()["channels"].find_one(
            {"_id": f"{guild_id}:tree:{scope}:{channel_key}"}
        )
        return str(document["channel_id"]) if document and document.get("channel_id") else None
    except Exception:
        return None


__all__ = [
    "persist_resolutions",
    "provision_all",
    "provision_scope",
    "stored_channel_id",
]
