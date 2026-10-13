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
    category_name = translate(spec.category_key, locale)
    category_id = await platform.ensure_category(category_name)
    resolved: dict[str, str] = {spec.scope: category_id}
    for decl in _sorted_channels(spec):
        name = translate(decl.label_key, locale)
        if decl.kind == "forum":
            channel_id = await platform.ensure_forum(name, category_id)
        else:
            channel_id = await platform.ensure_channel(name, category_id)
        await _apply_access(guild, channel_id, decl)
        resolved[decl.key] = channel_id
    return resolved


async def provision_all(
    guild: discord.Guild, locale: str, translate: Callable[[str, str], str]
) -> None:
    """Provision every registered scope folder (boot pass, best-effort)."""
    for spec in registered_scopes():
        try:
            await provision_scope(guild, spec, locale, translate)
        except Exception:
            logger.warning(
                "panels provisioning failed (scope %s, guild %s) — best-effort",
                spec.scope,
                guild.id,
                exc_info=True,
            )


__all__ = [
    "provision_all",
    "provision_scope",
]
