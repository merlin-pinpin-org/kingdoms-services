"""Discord wiring for the ChannelService platform seam (kingdoms-services#5).

Implements the :class:`~kingdoms.core.services.channel.ChannelsPlatform`
seam with real discord.py calls only — no business logic lives here.
"""

from __future__ import annotations

import logging
from typing import Any

import discord

logger = logging.getLogger("kingdoms.channels.discord")


class DiscordChannelsPlatform:
    """discord.py implementation of the channels platform seam."""

    def __init__(self, bot: discord.Client) -> None:
        """Keep a client reference for guild and channel lookups."""
        self._bot = bot

    async def _guild(self, guild_id: str) -> discord.Guild | None:
        guild = self._bot.get_guild(int(guild_id)) if guild_id.isdigit() else None
        if guild is None and guild_id.isdigit():
            try:
                guild = await self._bot.fetch_guild(int(guild_id))
            except Exception:
                return None
        return guild

    async def find_channel_by_name(self, guild_id: str, name: str) -> str | None:
        """Find a guild channel id by its exact name; None when absent."""
        guild = await self._guild(guild_id)
        if guild is None:
            return None
        channel = discord.utils.get(guild.text_channels, name=name)
        return str(channel.id) if channel is not None else None

    async def create_channel(self, guild_id: str, name: str) -> str:
        """Create a text channel; return its id."""
        guild = await self._guild(guild_id)
        if guild is None:
            raise RuntimeError(f"guild {guild_id} not reachable")
        channel = await guild.create_text_channel(name, reason=f"kingdoms: provision the {name} channel")
        return str(channel.id)

    async def channel_exists(self, guild_id: str, channel_id: str) -> bool:
        """Whether the channel still exists on the platform."""
        guild = await self._guild(guild_id)
        if guild is None or not channel_id.isdigit():
            return False
        return guild.get_channel(int(channel_id)) is not None

    async def apply_access_policy(
        self, guild_id: str, channel_id: str, policy: dict[str, Any]
    ) -> None:
        """Apply a category's declared access policy as overwrites (#57).

        The bot overwrite is set first so the bot can never lock itself
        out; then @everyone, then the declared roles (resolved by name
        through the guild's role list — logical keys map to role names).
        """
        guild = await self._guild(guild_id)
        if guild is None or not channel_id.isdigit():
            return
        channel = guild.get_channel(int(channel_id))
        if not isinstance(channel, discord.TextChannel):
            return
        if bool(policy.get("bot_overwrite", True)):
            await channel.set_permissions(
                guild.me,
                overwrite=discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, read_message_history=True, manage_messages=True
                ),
                reason="kingdoms: access policy bot overwrite",
            )
        everyone_view = bool(policy.get("everyone_view", True))
        everyone_post = bool(policy.get("everyone_post", False))
        await channel.set_permissions(
            guild.default_role,
            overwrite=discord.PermissionOverwrite(
                view_channel=everyone_view,
                send_messages=everyone_post if not everyone_view else None,
                read_message_history=everyone_view,
            ),
            reason="kingdoms: access policy everyone overwrite",
        )
        role_keys: list[str] = list(policy.get("view") or ()) + list(policy.get("post") or ())
        for role_key in role_keys:
            role = discord.utils.get(guild.roles, name=role_key)
            if role is None:
                continue
            await channel.set_permissions(
                role,
                overwrite=discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=role_key in (policy.get("post") or ()),
                    read_message_history=True,
                ),
                reason="kingdoms: access policy role overwrite",
            )

    async def ensure_channel(self, guild_id: str, name: str, category_id: str | None = None) -> str:
        """Find or create a text channel (under a category); return its id."""
        guild = await self._guild(guild_id)
        if guild is None:
            raise RuntimeError(f"guild {guild_id} not reachable")
        channel = discord.utils.get(guild.text_channels, name=name)
        if channel is None:
            parent = discord.utils.get(guild.categories, id=int(category_id)) if category_id else None
            channel = await guild.create_text_channel(
                name, category=parent, reason=f"kingdoms: provision the {name} channel"
            )
        return str(channel.id)

    async def ensure_category(self, guild_id: str, name: str) -> str:
        """Find or create a category channel; return its id (idempotent)."""
        guild = await self._guild(guild_id)
        if guild is None:
            raise RuntimeError(f"guild {guild_id} not reachable")
        category = discord.utils.get(guild.categories, name=name)
        if category is None:
            category = await guild.create_category(name, reason=f"kingdoms: provision the {name} category")
        return str(category.id)

    async def ensure_forum(self, guild_id: str, name: str, category_id: str | None = None) -> str:
        """Find or create a read-only forum under a category; return its id."""
        guild = await self._guild(guild_id)
        if guild is None:
            raise RuntimeError(f"guild {guild_id} not reachable")
        forum = discord.utils.get(guild.forums, name=name)
        if forum is None:
            parent = discord.utils.get(guild.categories, id=int(category_id)) if category_id else None
            forum = await guild.create_forum(
                name,
                category=parent,
                overwrites={
                    guild.default_role: discord.PermissionOverwrite(
                        view_channel=True, send_messages=False, create_public_threads=False
                    ),
                    guild.me: discord.PermissionOverwrite(
                        view_channel=True,
                        send_messages=True,
                        create_public_threads=True,
                        manage_threads=True,
                        read_message_history=True,
                    ),
                },
                reason=f"kingdoms: provision the {name} maps forum",
            )
        return str(forum.id)

    async def adopt_legacy_forum(
        self, guild_id: str, legacy_name: str, name: str, category_id: str | None = None
    ) -> str | None:
        """Rename a legacy forum to ``name`` when the target is absent.

        Migration helper: the first maps-forum iteration provisioned a
        forum named ``maps``; the generic one expects ``<game>-maps``.
        Returns the renamed forum id, or None when nothing to migrate.
        """
        guild = await self._guild(guild_id)
        if guild is None:
            raise RuntimeError(f"guild {guild_id} not reachable")
        if discord.utils.get(guild.forums, name=name) is not None:
            return None
        legacy = discord.utils.get(guild.forums, name=legacy_name)
        if legacy is None:
            return None
        if category_id is not None and str(legacy.category_id) != category_id:
            return None
        await legacy.edit(name=name, reason=f"kingdoms: migrate the {legacy_name} forum to {name}")
        return str(legacy.id)

    async def forum_thread_exists(self, guild_id: str, thread_id: str) -> bool:
        """Whether a forum thread still exists on the platform."""
        guild = await self._guild(guild_id)
        if guild is None or not thread_id.isdigit():
            return False
        return guild.get_channel_or_thread(int(thread_id)) is not None

    async def create_map_post(
        self,
        guild_id: str,
        forum_id: str,
        name: str,
        content: str,
        tags: list[str] | None = None,
        view: discord.ui.View | None = None,
    ) -> str:
        """Create one forum post (thread), optionally with a view; return its id."""
        guild = await self._guild(guild_id)
        if guild is None:
            raise RuntimeError(f"guild {guild_id} not reachable")
        forum = guild.get_channel(int(forum_id))
        if not isinstance(forum, discord.ForumChannel):
            raise RuntimeError(f"forum {forum_id} not reachable")
        available = {t.name for t in forum.available_tags}
        applied = [discord.ForumTag(name=t) for t in (tags or []) if t in available] or discord.utils.MISSING
        kwargs: dict[str, Any] = {"applied_tags": applied}
        if view is not None:
            kwargs["view"] = view
        thread, _ = await forum.create_thread(
            name=name,
            content=content,
            reason=f"kingdoms: map post {name}",
            **kwargs,
        )
        return str(thread.id)

    async def ensure_forum_post_view(
        self, guild_id: str, thread_id: str, view: discord.ui.View, marker_custom_id: str
    ) -> bool:
        """Attach ``view`` to a forum post's starter message when absent.

        Migration path for posts created before their flow existed: when
        the starter message carries no component matching ``marker_custom_id``
        (prefix match), edit it to attach the view. True when the post now
        carries it (already present or just attached).
        """
        guild = await self._guild(guild_id)
        if guild is None or not thread_id.isdigit():
            return False
        thread = guild.get_channel_or_thread(int(thread_id))
        if not isinstance(thread, discord.Thread):
            return False
        message = await thread.fetch_message(thread.id)
        for component in message.components:
            for child in getattr(component, "children", ()):
                if getattr(child, "custom_id", "").startswith(marker_custom_id):
                    return True
        await message.edit(view=view)
        return True

    async def get_channel_overwrites(
        self, guild_id: str, channel_id: str
    ) -> dict[str, dict[str, bool]] | None:
        """Actual permission overwrites: target id -> {permission: bool}."""
        guild = await self._guild(guild_id)
        if guild is None or not channel_id.isdigit():
            return None
        channel = guild.get_channel(int(channel_id))
        if not isinstance(channel, discord.TextChannel):
            return None
        result: dict[str, dict[str, bool]] = {}
        for target, overwrite in channel.overwrites.items():
            key = "@everyone" if target == guild.default_role else str(target.id)
            allow, deny = overwrite.pair()
            permissions: dict[str, bool] = {}
            if allow.view_channel:
                permissions["view_channel"] = True
            if deny.view_channel:
                permissions["view_channel"] = False
            if allow.send_messages:
                permissions["send_messages"] = True
            if deny.send_messages:
                permissions["send_messages"] = False
            result[key] = permissions
        return result
