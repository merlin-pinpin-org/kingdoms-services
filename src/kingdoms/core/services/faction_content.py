"""Localized content service: durable Mongo store + Redis cache (core).

Entity content (faction/map descriptions, per platform locale) is
seeded from a licence-compatible provider into Mongo — durable, per
locale — and read through the StateService (Redis) cache at post
build time: Redis first (hot), Mongo second (durable), provider
never at runtime.

Document shape (``faction_content`` collection)::

    {"_id": "<entity id>:<locale>", "entity_id": ..., "locale": ...,
     "name": ..., "summary": ..., "source_url": ..., "provider": ...}

The ids embed the visible catalog ids (``faction:aoe2:Franks:fr``),
so content is keyed by the same stable ids the panels' footers show.
"""

from __future__ import annotations

import logging
from typing import Any

from kingdoms.core.services.state import StateService

logger = logging.getLogger("kingdoms.core.faction_content")

CONTENT_COLLECTION = "faction_content"
CACHE_SCOPE = "faction_content"
CACHE_TTL_S = 86_400


class FactionContentService:
    """Per-locale entity content: cache-aside reads over the durable store."""

    def __init__(self, database: Any, state: StateService | None = None) -> None:
        """Store the seams: the Mongo database and the Redis-backed state."""
        self._database = database
        self._state = state

    async def store(self, content: dict[str, Any]) -> None:
        """Upsert one content document (idempotent by ``<entity id>:<locale>``)."""
        doc = dict(content)
        doc["_id"] = f"{content['entity_id']}:{content['locale']}"
        await self._database[CONTENT_COLLECTION].replace_one({"_id": doc["_id"]}, doc, upsert=True)
        await self._invalidate_cache(doc["entity_id"], doc["locale"])

    async def get(self, entity_id: str, locale: str) -> dict[str, Any] | None:
        """Resolve one entity's content: Redis first, then Mongo."""
        cached = await self._cache_get(entity_id, locale)
        if cached is not None:
            return cached
        doc = await self._database[CONTENT_COLLECTION].find_one({"_id": f"{entity_id}:{locale}"})
        if doc is not None:
            await self._cache_put(doc)
            return dict(doc)
        return None

    async def _cache_get(self, entity_id: str, locale: str) -> dict[str, Any] | None:
        if self._state is None:
            return None
        try:
            return await self._state.get_state(CACHE_SCOPE, f"{entity_id}:{locale}")
        except Exception:
            logger.debug("content cache read failed (%s)", entity_id, exc_info=True)
            return None

    async def _cache_put(self, doc: dict[str, Any]) -> None:
        if self._state is None:
            return
        try:
            await self._state.set_state(
                CACHE_SCOPE,
                f"{doc['entity_id']}:{doc['locale']}",
                {k: v for k, v in doc.items() if k != "_id"},
                ttl=CACHE_TTL_S,
            )
        except Exception:
            logger.debug("content cache write failed (%s)", doc["entity_id"], exc_info=True)

    async def _invalidate_cache(self, entity_id: str, locale: str) -> None:
        """Drop the cached entry after a store (next read reloads it)."""
        if self._state is None:
            return
        try:
            await self._state.delete_state(CACHE_SCOPE, f"{entity_id}:{locale}")
        except Exception:
            logger.debug("content cache delete failed (%s)", entity_id, exc_info=True)
