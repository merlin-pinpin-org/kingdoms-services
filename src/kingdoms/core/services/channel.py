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
from typing import Protocol

from kingdoms.core.interfaces.platform import IChannel
from kingdoms.core.models.channel import ChannelModel
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

    async def apply_access_policy(
        self, guild_id: str, channel_id: str, policy: dict[str, object]
    ) -> None:
        """Apply a category's declared access policy as permission overwrites."""
        ...

    async def get_channel_overwrites(
        self, guild_id: str, channel_id: str
    ) -> dict[str, dict[str, bool]] | None:
        """Actual permission overwrites: target id -> {permission: bool}."""
        ...


class ChannelsCache(Protocol):
    """Narrow cache seam (StateService); values are JSON dicts."""

    async def get_state(self, scope: str, key: str) -> dict[str, object] | None:
        """Read one cached value; None on miss (store may be down)."""
        ...

    async def set_state(
        self, scope: str, key: str, value: dict[str, object], ttl: int | None = None
    ) -> bool:
        """Write one cached value with a TTL (best-effort); True when written."""
        ...

    async def delete_state(self, scope: str, key: str) -> bool:
        """Drop one cached value (best-effort)."""
        ...


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

    @property
    def platform(self) -> ChannelsPlatform:
        """The platform seam (audit and sync reuse it, kingdoms-services#57)."""
        return self._platform

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

    async def get_channel_for_category(self, guild_id: str, category: str) -> IChannel:
        """Resolve a channel for a category (cache -> database -> creation).

        The platform seam guarantees existence of the returned channel id;
        this method returns the core-side channel view built from it.
        """
        channel_id = await self._resolve_channel_id(guild_id, category)
        return _ResolvedChannel(id=channel_id, name=self._category_name(category))

    async def _resolve_channel_id(self, guild_id: str, category: str) -> str:
        """Cache-aside resolution of one guild category to a channel id."""
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

        name = self._category_name(category)
        adopted = await self._platform.find_channel_by_name(guild_id, name)
        if adopted is not None:
            await self._persist(guild_id, category, adopted, name)
            return adopted

        created = await self._platform.create_channel(guild_id, name)
        await self._persist(guild_id, category, created, name)
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
        """Provision every channel category declared by a mod.

        Generic: reads the mod's declaration via ModRegistry and resolves
        each ``mod:key`` category with the same flow as any other category.
        """
        definition = self._registry.require(mod_name)
        channels: dict[str, IChannel] = {}
        for category_def in definition.channel_categories:
            category = f"{mod_name}:{category_def.key}"
            channels[category] = await self.get_channel_for_category(guild_id, category)
            try:
                await self._platform.apply_access_policy(
                    guild_id, channels[category].id, category_def.access.to_dict()
                )
            except Exception:
                logger.warning(
                    "ACCESS POLICY application failed (guild %s, category %s) — best-effort",
                    guild_id,
                    category,
                    exc_info=True,
                )
        return channels


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
