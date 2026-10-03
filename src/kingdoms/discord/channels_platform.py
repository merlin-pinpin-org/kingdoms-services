"""Discord wiring for the ChannelService platform seam (kingdoms-services#5).

Implements the :class:`~kingdoms.core.services.channel.ChannelsPlatform`
and :class:`~kingdoms.core.services.channel.StructuredChannelsPlatform`
seams with real discord.py calls only — no business logic lives here.
Discord normalizes channel names to slugs ("Âge sombre" -> "age-sombre"),
so name matching here is slug-based; the core stays name-exact.

All Discord channel/category creation of the platform lives in this seam
(kingdoms-services#175): feature code resolves through ChannelService and
never calls create_category/create_text_channel/create_forum itself.
"""

from __future__ import annotations

import logging
import unicodedata

import discord

logger = logging.getLogger("kingdoms.channels.discord")


def slugify(name: str) -> str:
    """Normalize a channel/category name the way Discord does."""
    decomposed = unicodedata.normalize("NFKD", name.lower())
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return without_accents.replace(" ", "-").strip("-")


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
        """Find a guild channel id by its (slug-normalized) name; None when absent."""
        guild = await self._guild(guild_id)
        if guild is None:
            return None
        wanted = slugify(name)
        candidates = [*guild.text_channels, *getattr(guild, "forums", [])]
        for channel in candidates:
            if slugify(getattr(channel, "name", "")) == wanted:
                return str(channel.id)
        return None

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


    # ------------------------------------------------------------------
    # StructuredChannelsPlatform: declaration-driven provisioning
    # (kingdoms-services#175). Every create_* call of the platform lives
    # here; feature code never provisions Discord objects directly.
    # ------------------------------------------------------------------

    async def find_group_by_name(self, guild_id: str, name: str) -> str | None:
        """Find a category channel id by its (slug-normalized) name."""
        guild = await self._guild(guild_id)
        if guild is None:
            return None
        wanted = slugify(name)
        for category in getattr(guild, "categories", []):
            if slugify(getattr(category, "name", "")) == wanted:
                return str(category.id)
        return None

    async def create_group(self, guild_id: str, name: str, position: int, admin_only: bool) -> str:
        """Create a category, denying @everyone when admin-only."""
        guild = await self._guild(guild_id)
        if guild is None:
            raise RuntimeError(f"guild {guild_id} not reachable")
        overwrites: dict[discord.Role | discord.Member | discord.Object, discord.PermissionOverwrite] = {}
        if admin_only:
            overwrites[guild.default_role] = discord.PermissionOverwrite(view_channel=False)
            if guild.me is not None:
                overwrites[guild.me] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
        category = await guild.create_category(
            name,
            reason=f"kingdoms: provision the {name} category",
            overwrites=overwrites,
            position=position,
        )
        return str(category.id)

    def _channels_of_kind(self, guild: discord.Guild, kind: str) -> list[object]:
        """Return the guild's live channels of a declared kind (forum vs text)."""
        if kind == "forum":
            return list(getattr(guild, "forums", []) or [])
        return list(guild.text_channels)

    async def find_channel_of_kind(
        self, guild_id: str, name: str, kind: str, group_id: str | None
    ) -> str | None:
        """Find a channel of the declared kind by name, inside a group."""
        guild = await self._guild(guild_id)
        if guild is None:
            return None
        wanted = slugify(name)
        category_id = int(group_id) if group_id and group_id.isdigit() else None
        for channel in self._channels_of_kind(guild, kind):
            if category_id is not None and getattr(channel, "category_id", None) != category_id:
                continue
            if slugify(getattr(channel, "name", "")) == wanted:
                return str(channel.id)
        return None

    async def create_channel_of_kind(
        self, guild_id: str, name: str, kind: str, group_id: str | None, admin_only: bool, position: int
    ) -> str:
        """Create a channel of the declared kind inside a group (category)."""
        guild = await self._guild(guild_id)
        if guild is None:
            raise RuntimeError(f"guild {guild_id} not reachable")
        overwrites: dict[discord.Role | discord.Member | discord.Object, discord.PermissionOverwrite] = {}
        if admin_only:
            overwrites[guild.default_role] = discord.PermissionOverwrite(view_channel=False)
        elif kind == "announce":
            overwrites[guild.default_role] = discord.PermissionOverwrite(view_channel=True, send_messages=False)
        if admin_only or kind == "announce":
            if guild.me is not None:
                overwrites[guild.me] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
        fetched = guild.get_channel(int(group_id)) if group_id and group_id.isdigit() else None
        category = fetched if isinstance(fetched, discord.CategoryChannel) else None
        if kind == "forum":
            channel: discord.ForumChannel | discord.TextChannel = await guild.create_forum(
                name,
                reason=f"kingdoms: provision the {name} forum",
                category=category,
                overwrites=overwrites,
                position=position,
            )
        else:
            channel = await guild.create_text_channel(
                name,
                reason=f"kingdoms: provision the {name} channel",
                category=category,
                overwrites=overwrites,
                position=position,
            )
        return str(channel.id)

    async def single_channel_in_group(self, guild_id: str, group_id: str, kind: str) -> str | None:
        """Return the group's single channel of a kind, whatever its name; None otherwise."""
        guild = await self._guild(guild_id)
        if guild is None or not group_id.isdigit():
            return None
        category_id = int(group_id)
        found: object | None = None
        for channel in self._channels_of_kind(guild, kind):
            if getattr(channel, "category_id", None) != category_id:
                continue
            if found is not None:
                return None  # not a singleton — the caller falls back to creation
            found = channel
        return str(found.id) if found is not None else None
