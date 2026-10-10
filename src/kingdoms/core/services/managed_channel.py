"""Core managed-channel service: one named, self-healing channel per surface.

Several bot surfaces need a **dedicated channel** provisioned the same
way (the admin home 🛡-bot-admins, the guild home 🏛-home): a
channel found by name, adopted when it already exists, created when it
does not, persisted (Mongo) and cached (Redis), with every resolution
re-applying the surface's visibility policy.

This service owns that provisioning, once, for every surface —
AdminChannelService and the home channel are two configured instances
of the same lifecycle (cache-aside: Redis → Mongo → adoption →
creation). The *policy* (who may see the channel) is a platform-seam
callback: the admin channel restricts to the bot-admins role, the home
channel is open to everyone.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from kingdoms.core.models.channel import ChannelModel

logger = logging.getLogger("kingdoms.core.managed_channel")

MANAGED_CHANNELS_COLLECTION = "channels"


class ManagedChannelPlatform(Protocol):
    """Narrow platform seam: find, create, check, policy one channel."""

    async def find_channel_by_name(self, guild_id: str, name: str) -> str | None:
        """Find a guild channel id by its exact name; None when absent."""
        ...

    async def create_channel(self, guild_id: str, name: str, reason: str) -> str:
        """Create the channel; return its id."""
        ...

    async def channel_exists(self, guild_id: str, channel_id: str) -> bool:
        """Whether the channel still exists on the platform."""
        ...

    async def apply_policy(self, guild_id: str, channel_id: str) -> None:
        """Apply the surface's visibility policy to the channel."""
        ...

    async def send_layout(self, guild_id: str, channel_id: str, layout: Any) -> str:
        """Deliver a UI SDK layout to the channel; the message id."""
        ...


class ManagedChannelDatabase(Protocol):
    """Narrow persistence seam (the shared channels collection)."""

    async def find_channel(self, guild_id: str, category: str) -> ChannelModel | None:
        """Find the persisted channel document for a guild category."""
        ...

    async def upsert_channel(self, channel: ChannelModel) -> None:
        """Insert or replace the channel document (idempotent provisioning)."""
        ...

    async def delete_channel(self, guild_id: str, category: str) -> bool:
        """Drop a stale channel document; True when one was removed."""
        ...


class ManagedChannelCache(Protocol):
    """Narrow cache seam (the StateService get_state/set_state pair)."""

    async def get_state(self, scope: str, key: str) -> dict[str, Any] | None:
        """Read a hot-state entry; None when missing or expired."""
        ...

    async def set_state(self, scope: str, key: str, value: dict[str, Any], ttl: int | None = None) -> bool:
        """Write a hot-state entry with a TTL; True when the write landed."""
        ...


class ManagedChannelService:
    """Provision and resolve one named managed channel (cache-aside)."""

    def __init__(
        self,
        platform: ManagedChannelPlatform,
        database: ManagedChannelDatabase,
        category: str,
        name: str,
        state: Any = None,
        cache_ttl: int = 300,
    ) -> None:
        self._platform = platform
        self._db = database
        self._category = category
        self._name = name
        self._state = state
        self._cache_ttl = cache_ttl

    async def _safe(self, coro: Any) -> Any:
        try:
            return await coro
        except Exception:
            logger.warning("MANAGED CHANNEL cache read failed — cache-aside continues", exc_info=True)
            return None

    async def _cache_key(self, guild_id: str) -> str:
        return f"managed_channel:{self._category}:{guild_id}"

    async def _cache_get(self, guild_id: str) -> str | None:
        if self._state is None:
            return None
        cached = await self._safe(self._state.get_state("channels", await self._cache_key(guild_id)))
        return str(cached.get("channel_id")) if cached and cached.get("channel_id") else None

    async def _cache_set(self, guild_id: str, channel_id: str) -> None:
        if self._state is None:
            return
        await self._safe(
            self._state.set_state(
                "channels", await self._cache_key(guild_id), {"channel_id": channel_id}, ttl=self._cache_ttl
            )
        )

    async def resolve_channel(self, guild_id: str) -> str | None:
        """Cache-aside resolution: Redis → Mongo → adoption → creation.

        Every resolution re-applies the surface's visibility policy — a
        manual override never survives a resolution.
        """
        if not guild_id:
            return None
        cached = await self._cache_get(guild_id)
        if cached and await self._platform.channel_exists(guild_id, cached):
            await self._apply_policy(guild_id, cached)
            return cached
        stored = await self._db.find_channel(guild_id, self._category)
        if stored is not None:
            if await self._platform.channel_exists(guild_id, stored.channel_id):
                await self._cache_set(guild_id, stored.channel_id)
                await self._apply_policy(guild_id, stored.channel_id)
                return stored.channel_id
            await self._db.delete_channel(guild_id, self._category)
        adopted = await self._platform.find_channel_by_name(guild_id, self._name)
        if adopted is not None:
            await self._persist(guild_id, adopted)
            await self._cache_set(guild_id, adopted)
            await self._apply_policy(guild_id, adopted)
            return adopted
        channel_id = await self._platform.create_channel(
            guild_id,
            self._name,
            reason=f"Kingdoms {self._name} channel",
        )
        await self._persist(guild_id, channel_id)
        await self._cache_set(guild_id, channel_id)
        await self._apply_policy(guild_id, channel_id)
        return channel_id

    async def set_channel(self, guild_id: str, channel_id: str) -> None:
        """Route the managed channel to an existing guild channel."""
        if not await self._platform.channel_exists(guild_id, channel_id):
            raise ValueError(f"channel {channel_id} does not exist in guild {guild_id}")
        await self._persist(guild_id, channel_id)
        await self._cache_set(guild_id, channel_id)
        await self._apply_policy(guild_id, channel_id)

    async def deliver(self, guild_id: str, layout: Any) -> str | None:
        """Deliver a layout to the managed channel (best-effort)."""
        channel_id = await self.resolve_channel(guild_id)
        if channel_id is None:
            return None
        try:
            return await self._platform.send_layout(guild_id, channel_id, layout)
        except Exception:
            logger.warning(
                "MANAGED CHANNEL delivery failed (guild %s, channel %s) — best-effort",
                guild_id,
                channel_id,
                exc_info=True,
            )
            return None

    async def _persist(self, guild_id: str, channel_id: str) -> None:
        await self._db.upsert_channel(
            ChannelModel(
                _id=f"{guild_id}:{self._category}",
                guild_id=guild_id,
                platform="discord",
                category=self._category,
                channel_id=channel_id,
                name=self._name,
            )
        )

    async def _apply_policy(self, guild_id: str, channel_id: str) -> None:
        """Apply the surface's visibility policy (best-effort)."""
        try:
            await self._platform.apply_policy(guild_id, channel_id)
        except Exception:
            logger.warning(
                "MANAGED CHANNEL policy failed (guild %s, channel %s) — best-effort",
                guild_id,
                channel_id,
                exc_info=True,
            )
