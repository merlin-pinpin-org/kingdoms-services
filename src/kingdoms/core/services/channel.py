"""ChannelService: channel category management.

Resolves any category key — platform-level (ChannelCategory enum) or
mod-scoped (``mod:key``, declared in mod YAML and exposed by ModRegistry)
— to a concrete channel. Resolution order: cache -> database -> platform
creation. The service is generic: it never enumerates mod categories.

Implemented in kingdoms-services#5. Mod-scoped categories and per-mod
provisioning: kingdoms-services#26, ADR-0003.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol

from kingdoms.core.interfaces.platform import IChannel
from kingdoms.core.models.channel import ChannelModel
from kingdoms.core.services.mod_registry import ModRegistry
from kingdoms.core.services.mod_definition import ChannelGroupDef

logger = logging.getLogger("kingdoms.channels")

CHANNELS_COLLECTION = "channels"
CACHE_TTL_SECONDS = 300


class ChannelsDatabase(Protocol):
    """Narrow async MongoDB seam the ChannelService depends on."""

    def __init__(self, database: object) -> None:
        """Wrap an async MongoDB database."""
        ...

    async def find_channel(self, guild_id: str, category: str) -> ChannelModel | None:
        """Find the persisted channel document for a guild category."""
        ...

    async def upsert_channel(self, channel: ChannelModel) -> None:
        """Insert or replace the channel document."""
        ...

    async def delete_channel(self, guild_id: str, category: str) -> bool:
        """Drop a channel document; True when one was removed."""
        ...


class ChannelsPlatform(Protocol):
    """Narrow platform seam: channel lookup, creation, existence."""

    async def find_channel_by_name(self, guild_id: str, name: str) -> str | None:
        """Find an existing channel by its exact name; None when absent."""
        ...

    async def create_channel(self, guild_id: str, name: str) -> str:
        """Create a text channel; return its id."""
        ...

    async def channel_exists(self, guild_id: str, channel_id: str) -> bool:
        """Whether the channel still exists on the platform."""
        ...


class ChannelsCache(Protocol):
    """Narrow cache seam (StateService); values are JSON dicts."""

    async def get_state(self, scope: str, key: str) -> dict[str, object] | None:
        """Read one cached value; None on miss (store may be down)."""
        ...

    async def set_state(self, scope: str, key: str, value: dict[str, object], ttl: int) -> None:
        """Write one cached value with a TTL (best-effort)."""
        ...

    async def delete_state(self, scope: str, key: str) -> bool:
        """Drop one cached value (best-effort)."""
        ...


class StructuredChannelsPlatform(Protocol):
    """Optional platform capability: declaration-driven provisioning.

    Implemented by the platform layer (e.g. DiscordChannelsPlatform);
    when present, ChannelService provisions mod channel groups, kinds
    and permissions from the declaration. Platforms without it keep the
    flat legacy flow (text channels, adopt-by-name).
    """

    async def find_group_by_name(self, guild_id: str, name: str) -> str | None:
        """Find an existing channel group (category) by name; None when absent."""
        ...

    async def create_group(self, guild_id: str, name: str, position: int, admin_only: bool) -> str:
        """Create a channel group (category); return its id."""
        ...

    async def find_channel_of_kind(
        self, guild_id: str, name: str, kind: str, group_id: str | None
    ) -> str | None:
        """Find an existing channel by name inside a group; None when absent."""
        ...

    async def create_channel_of_kind(
        self, guild_id: str, name: str, kind: str, group_id: str | None, admin_only: bool, position: int
    ) -> str:
        """Create a channel of the declared kind inside a group; return its id."""
        ...

    async def single_channel_in_group(self, guild_id: str, group_id: str, kind: str) -> str | None:
        """Return the group's single channel of a kind, whatever its name; None otherwise."""
        ...


@dataclass(frozen=True, slots=True)
class ChannelProvisionReport:
    """What a declaration-driven provisioning pass created and adopted."""

    created: tuple[str, ...] = field(default_factory=tuple)
    adopted: tuple[str, ...] = field(default_factory=tuple)
    group_ids: dict[str, str] = field(default_factory=dict)
    channel_ids: dict[str, str] = field(default_factory=dict)


class ChannelService:
    """Manage channels by category; never by name or ID."""

    def __init__(
        self,
        database: ChannelsDatabase,
        platform: ChannelsPlatform,
        cache: ChannelsCache,
        registry: ModRegistry,
    ) -> None:
        """Wire the stores; ``cache`` is a StateService (Redis cache-aside)."""
        self._db = database
        self._platform = platform
        self._cache = cache
        self._registry = registry

    def _category_name(self, category: str) -> str:
        """Resolve a category key to its channel display name.

        Platform-level categories use their enum name; mod-scoped keys
        (``mod:key``) resolve through the mod's declaration — an unknown
        key fails loudly (never provision an undeclared channel).
        """
        if ":" in category:
            mod_name, _, key = category.partition(":")
            definition = self._registry.require(mod_name)
            return definition.channel_category(key).display_name
        from kingdoms.core.enums.channel_category import ChannelCategory

        return ChannelCategory(category).name

    def _structured_platform(self) -> StructuredChannelsPlatform | None:
        """The platform's structured seam, when it implements it."""
        structured = self._platform
        for method in (
            "find_group_by_name",
            "create_group",
            "find_channel_of_kind",
            "create_channel_of_kind",
            "single_channel_in_group",
        ):
            if not hasattr(structured, method):
                return None
        return structured  # type: ignore[return-value]

    def _spec_for_category(
        self, category: str
    ) -> tuple[str, str, str, bool, int, str, bool]:
        """(name, kind, group key, admin_only, position, adopt, is_group).

        Mod-scoped keys read their declaration (group membership, kind,
        admin-only flag, adopt policy); group keys resolve as categories;
        platform categories keep the flat legacy flow.
        """
        if ":" in category:
            mod_name, _, key = category.partition(":")
            definition = self._registry.require(mod_name)
            for group in definition.channel_groups:
                if group.key == key:
                    return (group.display_name, "text", "", group.admin_only, group.position, "name", True)
            declared = definition.channel_category(key)
            return (
                declared.display_name,
                declared.kind,
                declared.group,
                declared.admin_only,
                declared.position,
                declared.adopt,
                False,
            )
        return (self._category_name(category), "text", "", False, 0, "name", False)

    def _group_def_for_category(self, category: str) -> ChannelGroupDef:
        """The ChannelGroupDef behind a ``mod:<group>`` category key."""
        mod_name, _, key = category.partition(":")
        return self._registry.require(mod_name).channel_group(key)

    async def _resolve_group_id(self, guild_id: str, group_category: str, group: ChannelGroupDef) -> str:
        """Resolve a declared channel group (category) to its platform id."""
        structured = self._structured_platform()
        if structured is None:
            return ""
        adopted = await structured.find_group_by_name(guild_id, group.display_name)
        if adopted is not None:
            await self._persist(guild_id, group_category, adopted, group.display_name)
            return adopted
        created = await structured.create_group(guild_id, group.display_name, group.position, group.admin_only)
        await self._persist(guild_id, group_category, created, group.display_name)
        return created

    async def get_channel_for_category(self, guild_id: str, category: str) -> IChannel:
        """Resolve a channel for a category (cache -> database -> creation).

        The platform seam guarantees existence of the returned channel id;
        this method returns the core-side channel view built from it.
        """
        channel_id = await self._resolve_channel_id(guild_id, category)
        return _ResolvedChannel(id=channel_id, name=self._category_name(category))

    async def _resolve_channel_id(
        self, guild_id: str, category: str, sink: dict[str, str] | None = None
    ) -> str:
        """Cache-aside resolution of one guild category to a channel id.

        ``sink`` (optional) records the outcome of the *resolution*
        (``created``/``adopted``) for declaration-driven provisioning.
        """
        cache_key = f"{guild_id}:{category}"
        cached = await self._cache.get_state(CHANNELS_COLLECTION, cache_key)
        if cached is not None and cached.get("channel_id"):
            channel_id = str(cached["channel_id"])
            if await self._platform.channel_exists(guild_id, channel_id):
                return channel_id
            await self._cache.delete_state(CHANNELS_COLLECTION, cache_key)

        stored = await self._db.find_channel(guild_id, category)
        if stored is not None:
            if await self._platform.channel_exists(guild_id, stored.channel_id):
                await self._store_cache(guild_id, category, stored.channel_id)
                return stored.channel_id
            await self._db.delete_channel(guild_id, category)

        name, kind, group_key, admin_only, position, adopt, is_group = self._spec_for_category(category)
        structured = self._structured_platform()
        if structured is not None and is_group:
            group_def = self._group_def_for_category(category)
            found = await structured.find_group_by_name(guild_id, group_def.display_name)
            if found is not None:
                await self._persist(guild_id, category, found, group_def.display_name)
                if sink is not None:
                    sink[category] = "adopted"
                return found
            created_group = await structured.create_group(
                guild_id, group_def.display_name, group_def.position, group_def.admin_only
            )
            await self._persist(guild_id, category, created_group, group_def.display_name)
            if sink is not None:
                sink[category] = "created"
            return created_group
        group_id: str | None = None
        if structured is not None and group_key:
            mod_name = category.partition(":")[0]
            group = self._registry.require(mod_name).channel_group(group_key)
            group_id = await self._resolve_group_id(guild_id, f"{mod_name}:{group_key}", group)

        if structured is not None and group_id is not None:
            found = await structured.find_channel_of_kind(guild_id, name, kind, group_id)
            if found is None and adopt == "group_single":
                found = await structured.single_channel_in_group(guild_id, group_id, kind)
            if found is not None:
                await self._persist(guild_id, category, found, name)
                if sink is not None:
                    sink[category] = "adopted"
                return found
            created = await structured.create_channel_of_kind(
                guild_id, name, kind, group_id, admin_only, position
            )
            await self._persist(guild_id, category, created, name)
            if sink is not None:
                sink[category] = "created"
            return created

        adopted = await self._platform.find_channel_by_name(guild_id, name)
        if adopted is not None:
            await self._persist(guild_id, category, adopted, name)
            if sink is not None:
                sink[category] = "adopted"
            return adopted

        created = await self._platform.create_channel(guild_id, name)
        await self._persist(guild_id, category, created, name)
        if sink is not None:
            sink[category] = "created"
        return created

    async def _persist(self, guild_id: str, category: str, channel_id: str, name: str) -> None:
        """Persist one resolution (database then cache)."""
        await self._db.upsert_channel(
            ChannelModel(
                _id=f"{guild_id}:{category}",
                guild_id=guild_id,
                platform="discord",
                category=category,
                channel_id=channel_id,
                name=name,
            )
        )
        await self._store_cache(guild_id, category, channel_id)

    async def _store_cache(self, guild_id: str, category: str, channel_id: str) -> None:
        """Cache one resolution with the standard TTL (best-effort)."""
        try:
            await self._cache.set_state(
                CHANNELS_COLLECTION,
                f"{guild_id}:{category}",
                {"channel_id": channel_id},
                CACHE_TTL_SECONDS,
            )
        except Exception:
            logger.warning("CHANNEL CACHE WRITE FAILED (guild %s, category %s)", guild_id, category)

    async def setup_mod_channels(self, guild_id: str, mod_name: str) -> dict[str, IChannel]:
        """Provision every channel group and channel declared by a mod.

        Generic: reads the mod's declaration via ModRegistry and resolves
        each ``mod:key`` category with the same flow as any other category.
        """
        definition = self._registry.require(mod_name)
        channels: dict[str, IChannel] = {}
        for group in definition.channel_groups:
            await self.get_channel_for_category(guild_id, f"{mod_name}:{group.key}")
        for category_def in definition.channel_categories:
            category = f"{mod_name}:{category_def.key}"
            channels[category] = await self.get_channel_for_category(guild_id, category)
        return channels

    async def provision_mod_channels(self, guild_id: str, mod_name: str) -> ChannelProvisionReport:
        """Provision a mod's whole declaration, reporting created/adopted.

        Declaration-driven (kingdoms-services#175): groups (categories)
        in declaration order, then each group's channels in declaration
        order — kinds, admin-only flags and positions all come from the
        YAML, never from feature code. Idempotent: existing groups and
        channels are adopted, never duplicated.
        """
        definition = self._registry.require(mod_name)
        sink: dict[str, str] = {}
        created: list[str] = []
        adopted: list[str] = []
        group_ids: dict[str, str] = {}
        channel_ids: dict[str, str] = {}
        for group in definition.channel_groups:
            group_category = f"{mod_name}:{group.key}"
            group_ids[group.key] = await self._resolve_channel_id(guild_id, group_category, sink)
            label = group.display_name
            (created if sink.get(group_category) == "created" else adopted).append(label)
        for category_def in definition.channel_categories:
            category = f"{mod_name}:{category_def.key}"
            channel_ids[category_def.key] = await self._resolve_channel_id(guild_id, category, sink)
            label = (
                f"{definition.channel_group(category_def.group).display_name}/{category_def.display_name}"
                if category_def.group
                else category_def.display_name
            )
            (created if sink.get(category) == "created" else adopted).append(label)
        return ChannelProvisionReport(
            created=tuple(created),
            adopted=tuple(adopted),
            group_ids=group_ids,
            channel_ids=channel_ids,
        )


class _ResolvedChannel:
    """Core-side channel view returned by the service (IChannel shape)."""

    def __init__(self, id: str, name: str) -> None:
        """Store the resolved identity."""
        self.id = id
        self.name = name

    def __repr__(self) -> str:
        """Render as the resolved channel identity."""
        return f"_ResolvedChannel(id={self.id!r}, name={self.name!r})"


def get_channel_service(
    database: ChannelsDatabase,
    platform: ChannelsPlatform,
    cache: ChannelsCache,
    registry: ModRegistry,
) -> ChannelService:
    """Build the ChannelService with its narrow seams wired."""
    return ChannelService(database=database, platform=platform, cache=cache, registry=registry)
