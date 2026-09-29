"""Platform message registry (kingdoms-services#130, reference §2.3).

The only place platform message IDs ever live. Mods register persistent
messages by logical key — ``(message_key, entity_id)`` scoped to a
platform — and resolve them back later to edit/refresh in place. Mod
collections never store message or channel IDs; they store logical keys.

Cache-aside resolution like the other platform services: Redis →
MongoDB. Registration is idempotent per key: re-registering a message
replaces the stored ID (messages are recreated, not patched, after a
channel wipe).

This registry replaces the #122 persistent-view reconstruction story:
reconstruction from ``custom_id`` survives component clicks, while the
registry lets a mod find *its own message back* to re-render it.
"""

from __future__ import annotations

import logging
from typing import Protocol

from kingdoms.core.models.registered_message import RegisteredMessageModel

logger = logging.getLogger("kingdoms.core.message_registry")

MESSAGES_COLLECTION = "platform_messages"
CACHE_TTL_SECONDS = 300


class MessagesDatabase(Protocol):
    """Narrow async MongoDB seam the MessageRegistryService depends on."""

    async def find_message(self, platform: str, message_key: str, entity_id: str) -> RegisteredMessageModel | None:
        """Find a registered message document; None when absent."""
        ...

    async def upsert_message(self, message: RegisteredMessageModel) -> None:
        """Insert or replace a registered message document."""
        ...

    async def delete_message(self, platform: str, message_key: str, entity_id: str) -> bool:
        """Drop a registered message document; True when one was removed."""
        ...


class MessagesCache(Protocol):
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


class MessageRegistryService:
    """Persist and resolve platform messages by logical key."""

    def __init__(self, database: MessagesDatabase, cache: MessagesCache) -> None:
        """Wire the persistence and cache seams."""
        self._database = database
        self._cache = cache

    async def register(
        self,
        *,
        platform: str,
        message_key: str,
        entity_id: str,
        channel_id: str,
        message_id: str,
        guild_id: str | None = None,
    ) -> None:
        """Persist (or replace) the message registered for a logical key."""
        message = RegisteredMessageModel(
            _id=f"{platform}:{message_key}:{entity_id}",
            platform=platform,
            message_key=message_key,
            entity_id=entity_id,
            channel_id=channel_id,
            message_id=message_id,
            guild_id=guild_id,
        )
        await self._database.upsert_message(message)
        await self._store_cache(platform, message_key, entity_id, channel_id, message_id)

    async def resolve(self, platform: str, message_key: str, entity_id: str) -> RegisteredMessageModel | None:
        """Resolve a registered message; None when never registered."""
        cached = await self._read_cache(platform, message_key, entity_id)
        if cached is not None:
            return RegisteredMessageModel(
                _id=f"{platform}:{message_key}:{entity_id}",
                platform=platform,
                message_key=message_key,
                entity_id=entity_id,
                channel_id=cached["channel_id"],
                message_id=cached["message_id"],
            )
        message = await self._database.find_message(platform, message_key, entity_id)
        if message is None:
            return None
        await self._store_cache(platform, message_key, entity_id, message.channel_id, message.message_id)
        return message

    async def forget(self, platform: str, message_key: str, entity_id: str) -> bool:
        """Drop a registration; True when one existed."""
        removed = await self._database.delete_message(platform, message_key, entity_id)
        try:
            await self._cache.delete_state(MESSAGES_COLLECTION, f"{platform}:{message_key}:{entity_id}")
        except Exception:
            logger.warning("MESSAGE CACHE DELETE FAILED (%s:%s)", message_key, entity_id)
        return removed

    async def _read_cache(self, platform: str, message_key: str, entity_id: str) -> dict[str, str] | None:
        try:
            cached = await self._cache.get_state(MESSAGES_COLLECTION, f"{platform}:{message_key}:{entity_id}")
        except Exception:
            logger.warning("MESSAGE CACHE READ FAILED (%s:%s)", message_key, entity_id)
            return None
        if cached is None or "channel_id" not in cached or "message_id" not in cached:
            return None
        return {"channel_id": str(cached["channel_id"]), "message_id": str(cached["message_id"])}

    async def _store_cache(
        self, platform: str, message_key: str, entity_id: str, channel_id: str, message_id: str
    ) -> None:
        """Cache one resolution with the standard TTL (best-effort)."""
        try:
            await self._cache.set_state(
                MESSAGES_COLLECTION,
                f"{platform}:{message_key}:{entity_id}",
                {"channel_id": channel_id, "message_id": message_id},
                CACHE_TTL_SECONDS,
            )
        except Exception:
            logger.warning("MESSAGE CACHE WRITE FAILED (%s:%s)", message_key, entity_id)
