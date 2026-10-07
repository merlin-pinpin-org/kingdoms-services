"""The generic mod-admin channel mechanism: one admin panel per mod.

A mod's **type is mandatory** in its declaration (``seasonal:
true|false``) and branches the admin surface — the two flavours of
the Mod interface: a **permanent mod** pins its config panel in a
channel inside its guild-level category; a **seasonal mod** pins its
lifecycle panel in a cross-season root channel plus one admin salon
per season. A seasonal mod wants two admin surfaces, and both are generic — every
mod can register them without writing channel plumbing:

- a **root admin channel** (e.g. ``🛡-ladder-admin``): cross-season, at
  the guild's root, visible to the mod's staff roles + bot-admins. It
  hosts the pinned **mod lifecycle panel** — for a seasonal mod this is
  the panel that creates seasons, activates one (it defines the active
  season), ends it and toggles the enrollments;

- a **season admin channel** (e.g. ``🛡-season-admin``): inside the
  season's own category (provisioned by the mod's channels sync), it
  hosts the pinned **per-season config panel** — the settings that live
  and die with the season.

A mod registers a single :class:`ModAdminChannelSpec` (channel names,
staff-role prefixes, and the two layout builders); the machinery below
provisions the channels (managed-channel, cache-aside), applies the
visibility policy and keeps the pinned panels alive (message registry,
no rebuild while the message lives — the pinned-menu contract).
Resolution is **id-based** everywhere: names only label the creation.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, cast

import discord

from kingdoms.core.services.pinned_menu import PinnedMenuChannel, PinnedMenuService

logger = logging.getLogger("kingdoms.mod_admin_channels")

RootLayoutBuilder = Callable[[discord.Client, str], Awaitable[discord.ui.LayoutView]]
SeasonLayoutBuilder = Callable[[discord.Client, str, str], Awaitable[discord.ui.LayoutView]]


@dataclass(frozen=True)
class ModAdminChannelSpec:
    """One mod's admin surfaces: the channel names + the layout builders.

    ``build_root_layout(bot, guild_id)`` renders the cross-season
    lifecycle panel; ``build_season_layout(bot, guild_id, scope)``
    renders the per-season config panel (``scope`` is the season's
    logical scope, e.g. ``s1``) — None when the mod is not seasonal.
    """

    mod: str
    channel_name: str
    seasonal: bool
    staff_role_prefixes: tuple[str, ...]
    guild_category_name: str = ""
    extra_roles: tuple[str, ...] = ("bot-admins",)
    build_root_layout: RootLayoutBuilder | None = None
    build_season_layout: SeasonLayoutBuilder | None = None
    season_channel_name: str = "🛡-season-admin"
    root_message_key: str = ""
    root_marker: str = ""
    root_category: str = ""

    def __post_init__(self) -> None:
        """Fill the derived keys (message key, marker, category) when unset."""
        keys = {
            "root_message_key": f"mod-{self.mod}-admin-menu",
            "root_marker": f"admin:{self.mod}:pin:",
            "root_category": f"mod_{self.mod}_admin",
        }
        for name, value in keys.items():
            if not getattr(self, name):
                object.__setattr__(self, name, value)

    @property
    def category_label(self) -> str:
        """Return the admin channel's channels-registry category key."""
        if self.seasonal:
            return self.root_category
        return f"mod_{self.mod}_guild_category"


def spec_registry(bot: discord.Client) -> dict[str, ModAdminChannelSpec]:
    """Return the bot's mod spec registry, created on demand."""
    registry = getattr(bot, "mod_admin_channel_specs", None)
    if not isinstance(registry, dict):
        registry = {}
        bot.mod_admin_channel_specs = registry  # type: ignore[attr-defined]
    return registry


def register_mod_admin_channel(bot: discord.Client, spec: ModAdminChannelSpec) -> None:
    """Register a mod's admin surfaces; the last registration wins."""
    spec_registry(bot)[spec.mod] = spec


def _root_pin_store(bot: discord.Client) -> dict[tuple[str, str], str]:
    """Return the in-memory fallback for the root pins (id by mod+guild)."""
    store = getattr(bot, "_mod_admin_menu_message_ids", None)
    if not isinstance(store, dict):
        store = {}
        bot._mod_admin_menu_message_ids = store  # type: ignore[attr-defined]
    return store


class ModAdminPinInteraction:
    """Interaction shim: guild + client is all the entry views read."""

    def __init__(self, guild_id: str, bot: Any) -> None:
        self.guild_id = int(guild_id) if guild_id.isdigit() else None
        self.client = bot


class ModAdminChannelPlatform:
    """discord.py seam: find, create, check, restrict a mod's admin channel."""

    def __init__(self, bot: discord.Client, spec: ModAdminChannelSpec) -> None:
        self._bot = bot
        self._spec = spec
        self._category_id: str | None = None

    async def _guild_category_id(self, guild: discord.Guild) -> str | None:
        """Resolve the mod's guild-level category id (cached, id-based)."""
        if not self._spec.guild_category_name:
            return None
        if self._category_id is not None and any(
            str(c.id) == self._category_id for c in getattr(guild, "categories", ())
        ):
            return self._category_id
        category = discord.utils.get(guild.categories, name=self._spec.guild_category_name)
        if category is None:
            category = await guild.create_category(
                self._spec.guild_category_name,
                reason=f"kingdoms: {self._spec.mod} guild category",
            )
        self._category_id = str(category.id)
        return self._category_id

    async def _guild(self, guild_id: str) -> discord.Guild | None:
        guild = self._bot.get_guild(int(guild_id)) if guild_id.isdigit() else None
        if guild is None and guild_id.isdigit():
            try:
                guild = await self._bot.fetch_guild(int(guild_id))
            except Exception:
                return None
        return guild

    async def find_channel_by_name(self, guild_id: str, name: str) -> str | None:
        """Find a text channel by name (first adoption only)."""
        guild = await self._guild(guild_id)
        if guild is None:
            return None
        channel = discord.utils.get(guild.text_channels, name=name)
        return str(channel.id) if channel is not None else None

    async def create_channel(self, guild_id: str, name: str, reason: str) -> str:
        """Create the mod's admin channel — inside the guild category for a permanent mod."""
        guild = await self._guild(guild_id)
        if guild is None:
            raise RuntimeError(f"guild {guild_id} not reachable")
        category_id = await self._guild_category_id(guild)
        parent = discord.utils.get(guild.categories, id=int(category_id)) if category_id else None
        channel = await guild.create_text_channel(name, category=parent, reason=reason)
        return str(channel.id)

    async def channel_exists(self, guild_id: str, channel_id: str) -> bool:
        """Whether the stored channel id still lives in the guild."""
        guild = await self._guild(guild_id)
        if guild is None or not channel_id.isdigit():
            return False
        return guild.get_channel(int(channel_id)) is not None

    async def apply_policy(self, guild_id: str, channel_id: str) -> None:
        """Visibility: the mod's staff roles + extra roles, nothing else."""
        guild = await self._guild(guild_id)
        if guild is None or not channel_id.isdigit():
            return
        channel = guild.get_channel(int(channel_id))
        if not isinstance(channel, discord.TextChannel):
            return
        await channel.set_permissions(
            guild.me,
            overwrite=discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True, manage_messages=True
            ),
            reason=f"kingdoms: {self._spec.mod} admin channel bot access",
        )
        await channel.set_permissions(
            guild.default_role,
            overwrite=discord.PermissionOverwrite(view_channel=False),
            reason=f"kingdoms: {self._spec.mod} admin channel staff-only",
        )
        wanted = self._spec.extra_roles
        for role in guild.roles:
            if not role.name.startswith(self._spec.staff_role_prefixes) and role.name not in wanted:
                continue
            await channel.set_permissions(
                role,
                overwrite=discord.PermissionOverwrite(
                    view_channel=True, read_message_history=True, send_messages=True
                ),
                reason=f"kingdoms: {self._spec.mod} admin channel staff access",
            )

    async def send_layout(self, guild_id: str, channel_id: str, layout: Any) -> str:
        """Send one layout into the channel; the created message id."""
        guild = await self._guild(guild_id)
        channel = guild.get_channel(int(channel_id)) if guild and channel_id.isdigit() else None
        if not isinstance(channel, discord.TextChannel):
            raise RuntimeError(f"{self._spec.mod} admin channel {channel_id} not reachable")
        message = await channel.send(view=layout)
        return str(message.id)


def build_mod_admin_channel_service(
    bot: discord.Client, spec: ModAdminChannelSpec, mongo_uri: str, redis_uri: str
) -> Any | None:
    """Wire one mod's root admin channel; None when stores are absent."""
    if not mongo_uri or not redis_uri:
        return None
    try:
        from kingdoms.core.models.db import get_async_database
        from kingdoms.core.services.managed_channel import ManagedChannelService
        from kingdoms.core.services.state import StateService
        from kingdoms.discord.logs_platform import MongoLogsDatabase

        return ManagedChannelService(
            platform=ModAdminChannelPlatform(bot, spec),
            database=MongoLogsDatabase(get_async_database()),
            category=spec.category_label,
            name=spec.channel_name,
            state=StateService(redis_uri=redis_uri),
        )
    except Exception:
        logger.exception("%s admin channel wiring failed — panel stays in the bot admin channel", spec.mod)
        return None


async def _resolve_root_message_id(
    bot: discord.Client, spec: ModAdminChannelSpec, guild_id: str
) -> str | None:
    """Resolve the registered root menu id (registry first, memory fallback)."""
    registry = getattr(bot, "message_registry", None)
    if registry is not None:
        try:
            registered = await registry.resolve("discord", spec.root_message_key, guild_id)
            if registered is not None:
                return str(registered.message_id)
        except Exception:
            logger.warning("%s admin menu registry resolve failed — best-effort", spec.mod)
    value = _root_pin_store(bot).get((spec.mod, guild_id))
    return str(value) if value is not None else None


async def _register_root_message(
    bot: discord.Client, spec: ModAdminChannelSpec, guild_id: str, channel_id: str, message_id: str
) -> None:
    """Persist the new root menu id: memory fallback + registry (durable)."""
    _root_pin_store(bot)[(spec.mod, guild_id)] = message_id
    registry = getattr(bot, "message_registry", None)
    if registry is None:
        return
    try:
        await registry.register(
            platform="discord",
            message_key=spec.root_message_key,
            entity_id=guild_id,
            channel_id=channel_id,
            message_id=message_id,
            guild_id=guild_id,
        )
    except Exception:
        logger.warning("%s admin menu registry register failed — best-effort", spec.mod)


async def _registered_menu_lives(bot: discord.Client, spec: ModAdminChannelSpec, guild_id: str, channel: Any) -> bool:
    """Whether the registered root menu still exists; re-pin when unpinned."""
    message_id = await _resolve_root_message_id(bot, spec, guild_id)
    if message_id is None:
        return False
    try:
        message = await channel.fetch_message(int(message_id))
    except Exception:
        return False
    try:
        await message.pin(reason=f"kingdoms: pinned {spec.mod} admin menu (mod staff home)")
    except Exception:
        logger.warning("%s admin menu re-pin failed — best-effort", spec.mod, exc_info=True)
    return True


async def ensure_pinned_mod_admin_menu(bot: discord.Client, spec: ModAdminChannelSpec, guild_id: str) -> bool:
    """Ensure the mod's root admin channel holds its pinned lifecycle panel.

    A live registered menu is merely re-pinned; only a gone message
    triggers a rebuild whose id replaces the registration.
    """
    service = getattr(bot, f"{spec.mod}_admin_channel_service", None)
    if service is None:
        return False
    channel_id = await service.resolve_channel(guild_id)
    if channel_id is None:
        return False
    guild = bot.get_guild(int(guild_id)) if guild_id.isdigit() else None
    channel = guild.get_channel(int(channel_id)) if channel_id.isdigit() and guild else None
    if channel is None or not hasattr(channel, "fetch_message") or not hasattr(channel, "send"):
        return False
    if await _registered_menu_lives(bot, spec, guild_id, channel):
        return False

    class _ChannelDelivery:
        last_message_id: str | None = None

        async def deliver(self, channel: Any, layout: Any) -> str:
            message = await channel.send(view=layout)
            self.last_message_id = str(message.id)
            return self.last_message_id

    delivery = _ChannelDelivery()
    pinned = PinnedMenuService(delivery)
    created = await pinned.ensure(
        guild_id,
        cast("PinnedMenuChannel", channel),
        marker=spec.root_marker,
        build_layout=lambda guild: _build_root_layout(bot, spec, guild),
        pin_reason=f"kingdoms: pinned {spec.mod} admin menu (mod staff home)",
    )
    if not created:
        return False
    if delivery.last_message_id is not None:
        await _register_root_message(bot, spec, guild_id, str(channel_id), delivery.last_message_id)
    return True


async def _build_root_layout(bot: discord.Client, spec: ModAdminChannelSpec, guild_id: str) -> discord.ui.LayoutView:
    """Build the pinned root layout through the mod's registered builder."""
    if spec.build_root_layout is None:
        view: discord.ui.LayoutView = discord.ui.LayoutView(timeout=None)
        return view
    return await spec.build_root_layout(bot, guild_id)


async def maintain_pinned_mod_admin_menus(bot: discord.Client) -> None:
    """Keep every registered mod's root admin menu alive (self-healing)."""
    import asyncio

    await asyncio.sleep(10)
    while True:
        for spec in list(spec_registry(bot).values()):
            for guild in list(bot.guilds):
                try:
                    await ensure_pinned_mod_admin_menu(bot, spec, str(guild.id))
                except Exception:
                    logger.warning(
                        "%s admin menu check failed (guild %s) — best-effort",
                        spec.mod,
                        guild.id,
                        exc_info=True,
                    )
        await asyncio.sleep(300)


async def ensure_pinned_season_admin_panel(
    spec: ModAdminChannelSpec, guild: Any, scope: str, channel: Any
) -> None:
    """Keep the pinned per-season config panel alive in the season's salon.

    The season admin channel is provisioned by the mod's channels sync
    (it lives inside the season's category); this helper only keeps the
    pinned panel alive in it, under a scope-addressed registry key.
    """
    if spec.build_season_layout is None or channel is None:
        return

    class _Delivery:
        async def deliver(self, channel: object, layout: object) -> str:
            message = await channel.send(view=layout)  # type: ignore[attr-defined]
            return str(message.id)

    async def _build(guild_id: str) -> object:
        builder = spec.build_season_layout
        if builder is None:
            return discord.ui.LayoutView(timeout=None)
        return await builder(guild.client, guild_id, scope)

    service = PinnedMenuService(cast("Any", _Delivery()))
    await service.ensure(
        f"{guild.id}:{scope}",
        cast("PinnedMenuChannel", channel),
        marker=f"{spec.root_marker}season:",
        build_layout=_build,
        pin_reason=f"kingdoms: pinned {spec.mod} season admin panel (season {scope})",
    )


@dataclass
class SeasonAdminPanelRef:
    """A resolved season admin channel reference (id-based, never names)."""

    channel_id: str
    scope: str
    extra: dict[str, str] = field(default_factory=dict)
