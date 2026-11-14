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
from typing import Any

import discord

from botforge.discord.ui.panels import ChannelDecl, ScopeSpec, registered_scopes

logger = logging.getLogger("botforge.discord.panels_provisioning")

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
        created = await self._guild.create_category(name, reason="botforge: panels provisioning")
        return str(created.id)

    async def ensure_channel(self, name: str, category_id: str) -> str:
        """Return the text channel id, creating it when missing."""
        category = self._category(category_id)
        # category-scoped FIRST (two scopes may declare the same channel
        # NAME — the games and mods catalogs are both "catalogue": a
        # name match in ANOTHER folder is not this channel); a stray
        # channel with no folder is still ADOPTED (moved into place)
        channel = discord.utils.get(
            self._guild.text_channels, name=name, category=(category or None)
        ) or discord.utils.get(
            self._guild.text_channels, name=name, category=None
        )
        if channel is not None:
            if category is not None and channel.category_id != category.id:
                try:
                    await channel.edit(category=category, reason="botforge: panels provisioning (adopt)")
                except discord.HTTPException:
                    logger.warning(
                        "panels provisioning: moving %s into its folder failed — kept in place",
                        name,
                        exc_info=True,
                    )
            return str(channel.id)
        created = await self._guild.create_text_channel(
            name, category=category, reason="botforge: panels provisioning"
        )
        return str(created.id)

    async def ensure_forum(self, name: str, category_id: str) -> str:
        """Return the forum id, creating it when missing."""
        category = self._category(category_id)
        # category-scoped first (see ensure_channel); a stray forum is
        # still adopted (moved into its folder)
        forum = discord.utils.get(
            self._guild.forums, name=name, category=(category or None)
        ) or discord.utils.get(self._guild.forums, name=name, category=None)
        if forum is not None:
            if category is not None and forum.category_id != category.id:
                try:
                    await forum.edit(category=category, reason="botforge: panels provisioning (adopt)")
                except discord.HTTPException:
                    logger.warning(
                        "panels provisioning: moving %s into its folder failed — kept in place",
                        name,
                        exc_info=True,
                    )
            return str(forum.id)
        created = await self._guild.create_forum(
            name, category=category, reason="botforge: panels provisioning"
        )
        return str(created.id)


async def _apply_access(guild: discord.Guild, channel_id: str, decl: ChannelDecl) -> None:
    """Apply the channel's access band (overwrites, best-effort).

    The bot's own member always carries an explicit allow (view, send,
    manage) — without it a ``view_channel=False`` for @everyone locks
    the bot out of the very channel it provisioned, and every surface
    (pins, syncs, dashboards) degrades with 403s.
    """
    channel = guild.get_channel(int(channel_id)) if channel_id.isdigit() else None
    if channel is None:
        return
    try:
        me = guild.me
        if me is not None:
            await channel.set_permissions(
                me,
                overwrite=discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    manage_messages=True,
                    manage_threads=True,
                    read_message_history=True,
                    embed_links=True,
                    attach_files=True,
                ),
                reason="botforge: the bot operates its own channel",
            )
        policy = getattr(decl, "policy", None)
        if policy is not None:
            # the DECLARED policy (the salons-as-data gating, #216
            # tranche I): @everyone's view is data, the view roles are
            # data — the roles resolve by their provisioned display
            # name (the mod's roles are provisioned from the same
            # declaration)
            if not getattr(policy, "everyone_view", True):
                await channel.set_permissions(
                    guild.default_role,
                    overwrite=discord.PermissionOverwrite(view_channel=False),
                    reason="botforge: the declared policy gates @everyone",
                )
            for role_name in getattr(policy, "view", ()) or ():
                role = discord.utils.get(guild.roles, name=str(role_name))
                if role is None:
                    logger.warning(
                        "panels provisioning: the view role %r is not provisioned — skipped",
                        role_name,
                    )
                    continue
                await channel.set_permissions(
                    role,
                    overwrite=discord.PermissionOverwrite(
                        view_channel=True,
                        send_messages=not getattr(policy, "everyone_post", False),
                        read_message_history=True,
                    ),
                    reason="botforge: the declared policy grants the view role",
                )
        elif decl.access == "readonly":
            overwrite = discord.PermissionOverwrite(send_messages=False, add_reactions=True)
            await channel.set_permissions(
                guild.default_role, overwrite=overwrite, reason="botforge: readonly menu channel"
            )
        elif decl.access == "admin":
            overwrite = discord.PermissionOverwrite(view_channel=False)
            await channel.set_permissions(
                guild.default_role, overwrite=overwrite, reason="botforge: admin channel"
            )
    except Exception:
        logger.warning(
            "panels provisioning: access apply failed (%s) — best-effort",
            decl.key,
            exc_info=True,
        )


async def _ensure_scope_category(
    guild: discord.Guild,
    platform: _Platform,
    scope: str,
    name: str,
) -> str:
    """Resolve (or create) the scope's category by stored id, then name.

    The stored id wins: a label refresh (an emoji added to the label)
    renames the category in place — never a duplicate. Without a
    stored id the name resolves or creates, and the caller persists.
    """
    stored = await stored_channel_id(str(guild.id), scope, "category")
    if stored and stored.isdigit():
        category = guild.get_channel(int(stored))
        if isinstance(category, discord.CategoryChannel):
            if category.name != name:
                await category.edit(name=name, reason="botforge: panels provisioning (label refresh)")
            return stored
    return await platform.ensure_category(name)


def _category_matches(channel: Any, category_id: str) -> bool:
    """Whether a live channel lives in the scope's declared folder.

    A scope without a category (the guild root) accepts anything; a
    scoped channel found in ANOTHER folder is not this scope's channel
    (the merlin case: the mods catalog's stored id pointed at the
    games forum — a valid forum, in the wrong folder).
    """
    if not category_id:
        return True
    return str(getattr(channel, "category_id", "") or "") == category_id


def _kind_matches(channel: Any, kind: str) -> bool:
    """Whether a live channel's Discord type matches the declared kind."""
    import discord

    if kind == "forum":
        return isinstance(channel, discord.ForumChannel)
    if kind == "text":
        return isinstance(channel, discord.TextChannel) and not isinstance(channel, discord.ForumChannel)
    return True


async def _decl_category(
    guild: discord.Guild,
    platform: _Platform,
    spec: ScopeSpec,
    decl: ChannelDecl,
    category_id: str,
    group_categories: dict[str, str],
    resolved: dict[str, str],
) -> str:
    """Resolve one channel's category: its declared group's, or the scope's.

    The group categories are created lazily (once per group) and ride
    the resolved dict — ``persist_resolutions`` stores them under
    ``tree:<scope>:cat:<group>``.
    """
    group_name = str(getattr(decl, "group", "") or "")
    if not group_name:
        return category_id
    channel_category = group_categories.get(group_name, "")
    if not channel_category:
        channel_category = await _ensure_group_category(guild, platform, spec.scope, group_name)
        group_categories[group_name] = channel_category
        resolved[f"cat:{group_name}"] = channel_category
    return channel_category


async def _ensure_group_category(
    guild: discord.Guild,
    platform: _Platform,
    scope: str,
    group_name: str,
) -> str:
    """Resolve (or create) one GROUP category of a scope (the mods' structure).

    Stored under ``cat:<group>`` inside the scope — the group's
    channels keep their own resolution keys; a label refresh renames
    in place, never a duplicate.
    """
    storage_key = f"cat:{group_name}"
    stored = await stored_channel_id(str(guild.id), scope, storage_key)
    if stored and stored.isdigit():
        category = guild.get_channel(int(stored))
        if isinstance(category, discord.CategoryChannel):
            if category.name != group_name:
                await category.edit(
                    name=group_name, reason="botforge: panels provisioning (label refresh)"
                )
            return stored
    for category in guild.categories:
        if category.name == group_name:
            return str(category.id)
    return await platform.ensure_category(group_name)


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
        category_id = await _ensure_scope_category(guild, platform, spec.scope, category_name)
        resolved["category"] = category_id
    # the GROUP categories (the mods' salons-as-data structure, #216
    # tranche I): a channel declaring a group lives in the group's own
    # category — created lazily, stored under cat:<group>, the channel
    # resolution keys stay tree:<scope>:<key> (the doors keep working)
    group_categories: dict[str, str] = {}
    # an empty category_key is the guild root: its channels live uncategorised
    for decl in _sorted_channels(spec):
        name = translate(decl.label_key, locale)
        channel_category = await _decl_category(
            guild,
            platform,
            spec,
            decl,
            category_id,
            group_categories,
            resolved,
        )
        try:
            stored = await stored_channel_id(str(guild.id), spec.scope, decl.key)
            if stored and stored.isdigit():
                channel = guild.get_channel(int(stored))
                if (
                    channel is not None
                    and _kind_matches(channel, decl.kind)
                    and _category_matches(channel, channel_category)
                ):
                    existing = getattr(channel, "name", "")
                    if existing != name:
                        await channel.edit(
                            name=name, reason="botforge: panels provisioning (label refresh)"
                        )
                    await _apply_access(guild, stored, decl)
                    resolved[decl.key] = stored
                    continue
                # a stored channel of the WRONG kind (a corrupted
                # provisioning, a manual conversion): it is treated as
                # absent — a fresh channel of the declared kind replaces
                # it and its stored id is overwritten (self-healing)
                if channel is not None:
                    logger.warning(
                        "panels provisioning: stored channel %s/%s has the wrong kind "
                        "(declared %s, found %s) — recreating",
                        spec.scope,
                        decl.key,
                        decl.kind,
                        type(channel).__name__,
                    )
            if decl.kind == "forum":
                channel_id = await platform.ensure_forum(name, channel_category)
            else:
                channel_id = await platform.ensure_channel(name, channel_category)
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
        from botforge.core.models.db import get_async_database

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
        from botforge.core.models.db import get_async_database

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
