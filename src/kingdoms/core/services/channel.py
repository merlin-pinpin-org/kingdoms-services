"""ChannelService: channel category management.

Resolves any category key — platform-level (ChannelCategory enum) or
mod-scoped (`mod:key`, declared in mod YAML and exposed by ModRegistry)
— to a concrete channel. Resolution order: cache -> database -> platform
creation. The service is generic: it never enumerates mod categories.

Implemented in kingdoms-services#5. Mod-scoped categories and per-mod
provisioning: kingdoms-services#26, ADR-0003. Declaration-driven channel
groups, kinds and adopt policies: kingdoms-services#175.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol

from kingdoms.core.interfaces.platform import IChannel
from kingdoms.core.models.channel import ChannelModel
from kingdoms.core.services.mod_definition import ChannelGroupDef
from kingdoms.core.services.mod_registry import ModRegistry

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
        """Wire the stores; `cache` is a StateService (Redis cache-aside)."""
        self._db = database
        self._platform = platform
        self._cache = cache
        self._registry = registry

    def _category_name(self, category: str) -> str:
        """Resolve a category key to its channel display name.

        Platform-level categories use their enum name; mod-scoped keys
        (`mod:key`) resolve through the mod's declaration — an unknown
        key fails loudly (never provision an undeclared channel).
        """
        if ":" in category:
            mod_name, _, key = category.partition(":")
            definition = self._registry.require(mod_name)
            return definition.channel_category(key).display_name
        from kingdoms.core.enums.channel_category import ChannelCategory

        return ChannelCategory(category).name

    def _structured_platform(self) -> StructuredChannelsPlatform | None:
        """Return the platform's structured seam, when it implements it."""
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
        self, category: str, *, as_group: bool
    ) -> tuple[str, str, str, bool, int, str]:
        """(name, kind, group key, admin_only, position, adopt) for one key.

        `as_group` decides what a same-key group/channel pair means
        (`mod:epoch` declares both): the caller states whether it asks
        for the group or the channel — a group key never resolves as a
        channel and vice versa. Mod-scoped keys read their declaration
        (group membership, kind, admin-only flag, adopt policy);
        platform categories keep the flat legacy flow.
        """
        if ":" in category:
            mod_name, _, key = category.partition(":")
            definition = self._registry.require(mod_name)
            if as_group:
                group = definition.channel_group(key)
                return (group.display_name, "text", "", group.admin_only, group.position, "name")
            declared = definition.channel_category(key)
            return (
                declared.display_name,
                declared.kind,
                declared.group,
                declared.admin_only,
                declared.position,
                declared.adopt,
            )
        return (self._category_name(category), "text", "", False, 0, "name")

    async def _resolve_group_id(self, guild_id: str, group: ChannelGroupDef) -> str | None:
        """Resolve a declared channel group to its platform id.

        Groups are containers, not channels: they resolve through the
        structured seam on every call (find by name, else create) and
        are never persisted in the channel stores.
        """
        structured = self._structured_platform()
        if structured is None:
            return None
        found = await structured.find_group_by_name(guild_id, group.display_name)
        if found is not None:
            return found
        return await structured.create_group(guild_id, group.display_name, group.position, group.admin_only)

    async def get_channel_for_category(
        self, guild_id: str, category: str, *, as_group: bool = False
    ) -> IChannel:
        """Resolve a channel (or, with `as_group`, a group) for a category.

        Channels resolve cache -> database -> creation; groups resolve
        through the structured seam only. The platform seam guarantees
        existence of the returned channel id; this method returns the
        core-side channel view built from it.
        """
        channel_id = await self._resolve_channel_id(guild_id, category, as_group=as_group)
        return _ResolvedChannel(id=channel_id, name=self._spec_for_category(category, as_group=as_group)[0])

    async def _resolve_channel_id(
        self,
        guild_id: str,
        category: str,
        *,
        as_group: bool = False,
        sink: dict[str, str] | None = None,
    ) -> str:
        """Resolve one guild category to a platform id.

        Channels resolve cache -> database -> platform creation. Groups
        (Discord categories) resolve through the structured seam only
        and never enter the channel stores: a group is a container, not
        a channel, and a same-key group/channel pair (`mod:epoch`)
        must never collide in the cache or the database. `sink`
        (optional) records the outcome (created/adopted) for
        declaration-driven provisioning.
        """
        name, _kind, group_key, _admin_only, _position, _adopt = self._spec_for_category(
            category, as_group=as_group
        )
        structured = self._structured_platform()

        if as_group:
            return await self._resolve_group_category(
                guild_id, category, structured, sink
            )

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

        if structured is not None and group_key:
            resolved = await self._resolve_in_group(
                guild_id, category, group_key, structured, sink
            )
            if resolved is not None:
                return resolved

        return await self._resolve_flat(guild_id, category, name, sink)

    async def _resolve_group_category(
        self,
        guild_id: str,
        category: str,
        structured: StructuredChannelsPlatform | None,
        sink: dict[str, str] | None,
    ) -> str:
        """Resolve a declared group (a container, never persisted) to its id."""
        name, _kind, _group, admin_only, position, _adopt = self._spec_for_category(
            category, as_group=True
        )
        if structured is None:
            # Legacy platform without the structured seam: the group
            # degrades to a flat channel under its display name.
            return await self._resolve_flat(guild_id, category, name, sink)
        found = await structured.find_group_by_name(guild_id, name)
        if found is not None:
            if sink is not None:
                sink[category] = "adopted"
            return found
        created_group = await structured.create_group(guild_id, name, position, admin_only)
        if sink is not None:
            sink[category] = "created"
        return created_group

    async def _resolve_in_group(
        self,
        guild_id: str,
        category: str,
        group_key: str,
        structured: StructuredChannelsPlatform,
        sink: dict[str, str] | None,
    ) -> str | None:
        """Resolve a channel inside its declared group; None to fall back flat."""
        name, kind, _group_key, admin_only, position, adopt = self._spec_for_category(
            category, as_group=False
        )
        mod_name = category.partition(":")[0]
        group = self._registry.require(mod_name).channel_group(group_key)
        group_id = await self._resolve_group_id(guild_id, group)
        if group_id is None:
            return None
        found = await structured.find_channel_of_kind(guild_id, name, kind, group_id)
        if found is None and adopt == "group_single":
            found = await structured.single_channel_in_group(guild_id, group_id, kind)
        if found is not None:
            await self._persist(guild_id, category, found, name)
            if sink is not None:
                sink[category] = "adopted"
            return found
        created = await structured.create_channel_of_kind(
            guild_id, name, kind, group_id, admin_only or group.admin_only, position
        )
        await self._persist(guild_id, category, created, name)
        if sink is not None:
            sink[category] = "created"
        return created

    async def _resolve_group_category(
        self,
        guild_id: str,
        category: str,
        structured: StructuredChannelsPlatform | None,
        sink: dict[str, str] | None,
    ) -> str:
        """Resolve a declared group (a container, never persisted) to its id."""
        name, _kind, _group, admin_only, position, _adopt = self._spec_for_category(
            category, as_group=True
        )
        if structured is None:
            # Legacy platform without the structured seam: the group
            # degrades to a flat channel under its display name.
            return await self._resolve_flat(guild_id, category, name, sink)
        found = await structured.find_group_by_name(guild_id, name)
        if found is not None:
            if sink is not None:
                sink[category] = "adopted"
            return found
        created_group = await structured.create_group(guild_id, name, position, admin_only)
        if sink is not None:
            sink[category] = "created"
        return created_group

    async def _resolve_in_group(
        self,
        guild_id: str,
        category: str,
        group_key: str,
        structured: StructuredChannelsPlatform,
        sink: dict[str, str] | None,
    ) -> str | None:
        """Resolve a channel inside its declared group; None to fall back flat."""
        name, kind, _group_key, admin_only, position, adopt = self._spec_for_category(
            category, as_group=False
        )
        mod_name = category.partition(":")[0]
        group = self._registry.require(mod_name).channel_group(group_key)
        group_id = await self._resolve_group_id(guild_id, group)
        if group_id is None:
            return None
        found = await structured.find_channel_of_kind(guild_id, name, kind, group_id)
        if found is None and adopt == "group_single":
            found = await structured.single_channel_in_group(guild_id, group_id, kind)
        if found is not None:
            await self._persist(guild_id, category, found, name)
            if sink is not None:
                sink[category] = "adopted"
            return found
        created = await structured.create_channel_of_kind(
            guild_id, name, kind, group_id, admin_only or group.admin_only, position
        )
        await self._persist(guild_id, category, created, name)
        if sink is not None:
            sink[category] = "created"
        return created

    async def _resolve_group_category(
        self,
        guild_id: str,
        category: str,
        structured: StructuredChannelsPlatform | None,
        sink: dict[str, str] | None,
    ) -> str:
        """Resolve a declared group (a container, never persisted) to its id."""
        name, _kind, _group, admin_only, position, _adopt = self._spec_for_category(
            category, as_group=True
        )
        if structured is None:
            # Legacy platform without the structured seam: the group
            # degrades to a flat channel under its display name.
            return await self._resolve_flat(guild_id, category, name, sink)
        found = await structured.find_group_by_name(guild_id, name)
        if found is not None:
            if sink is not None:
                sink[category] = "adopted"
            return found
        created_group = await structured.create_group(guild_id, name, position, admin_only)
        if sink is not None:
            sink[category] = "created"
        return created_group

    async def _resolve_in_group(
        self,
        guild_id: str,
        category: str,
        group_key: str,
        structured: StructuredChannelsPlatform,
        sink: dict[str, str] | None,
    ) -> str | None:
        """Resolve a channel inside its declared group; None to fall back flat."""
        name, kind, _group_key, admin_only, position, adopt = self._spec_for_category(
            category, as_group=False
        )
        mod_name = category.partition(":")[0]
        group = self._registry.require(mod_name).channel_group(group_key)
        group_id = await self._resolve_group_id(guild_id, group)
        if group_id is None:
            return None
        found = await structured.find_channel_of_kind(guild_id, name, kind, group_id)
        if found is None and adopt == "group_single":
            found = await structured.single_channel_in_group(guild_id, group_id, kind)
        if found is not None:
            await self._persist(guild_id, category, found, name)
            if sink is not None:
                sink[category] = "adopted"
            return found
        created = await structured.create_channel_of_kind(
            guild_id, name, kind, group_id, admin_only or group.admin_only, position
        )
        await self._persist(guild_id, category, created, name)
        if sink is not None:
            sink[category] = "created"
        return created

    async def _resolve_flat(
        self, guild_id: str, category: str, name: str, sink: dict[str, str] | None
    ) -> str:
        """Legacy flat seam: adopt by exact name, else create; persist the outcome."""
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
        each `mod:key` category with the same flow as any other category.
        """
        definition = self._registry.require(mod_name)
        channels: dict[str, IChannel] = {}
        for group in definition.channel_groups:
            await self.get_channel_for_category(guild_id, f"{mod_name}:{group.key}", as_group=True)
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
            group_ids[group.key] = await self._resolve_channel_id(
                guild_id, group_category, as_group=True, sink=sink
            )
            label = group.display_name
            (created if sink.get(group_category) == "created" else adopted).append(label)
        for category_def in definition.channel_categories:
            category = f"{mod_name}:{category_def.key}"
            channel_ids[category_def.key] = await self._resolve_channel_id(
                guild_id, category, as_group=False, sink=sink
            )
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
