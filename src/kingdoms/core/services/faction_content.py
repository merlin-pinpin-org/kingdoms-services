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
import re
from typing import Any

from kingdoms.core.services.state import StateService

logger = logging.getLogger("kingdoms.core.faction_content")

CONTENT_COLLECTION = "faction_content"
CACHE_SCOPE = "faction_content"
CACHE_TTL_S = 86_400
LIST_CACHE_SCOPE = "faction_content_list"
LIST_CACHE_TTL_S = 3_600


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
        await self._invalidate_list_caches(doc["entity_id"], doc["locale"])

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

    async def ensure_indexes(self) -> None:
        """Create the query indexes of the content collection.

        ``tags`` backs the Liquipedia map filters (``terrain:hybrid``,
        ``water``...) — a multikey index on the array; the covered
        ``(entity_id, locale)`` pair backs the guild-scoped map
        listings. Idempotent (Mongo ``create_index`` is a no-op on an
        existing index); safe to call from every sync/CLI run.
        """
        collection = self._database[CONTENT_COLLECTION]
        await collection.create_index("tags")
        await collection.create_index([("entity_id", 1), ("locale", 1)])

    async def query_by_tags(
        self, tags: list[str], entity_prefix: str = "", locale: str = ""
    ) -> list[dict[str, Any]]:
        """List the content docs carrying every given tag.

        ``entity_prefix`` scopes the query (``map:aoe2:`` — only maps,
        not factions); ``locale`` filters when non-empty. Returns the
        raw documents (they carry ``tags``, ``summary``, ``image_url``
        and the provider fields).
        """
        query: dict[str, Any] = {"tags": {"$all": tags}}
        if entity_prefix:
            query["entity_id"] = {"$regex": f"^{re.escape(entity_prefix)}"}
        if locale:
            query["locale"] = locale
        cursor = self._database[CONTENT_COLLECTION].find(query)
        return [dict(doc) async for doc in cursor]

    async def list_map_content(
        self,
        entity_ids: list[str],
        locale: str,
        cache_key: str = "",
    ) -> list[dict[str, Any]]:
        """List the stored content of a guild-visible set of maps.

        The guild's map list is its catalog scope (guild-local +
        global maps) — many guilds share the public maps, so the whole
        resolved list is cached in Redis under ``cache_key`` (one key
        per guild+locale) before hitting Mongo. The tracker set below
        ``tracker:<entity>:<locale>`` records every cache key an
        entity landed in; a store of that entity deletes them.
        """
        if cache_key and self._state is not None:
            try:
                cached = await self._state.get_state(LIST_CACHE_SCOPE, cache_key)
                if cached is not None and isinstance(cached, dict):
                    return list(cached.get("docs", []))
            except Exception:
                logger.debug("content list cache read failed (%s)", cache_key, exc_info=True)
        wanted = set(entity_ids)
        found: list[dict[str, Any]] = []
        for entity_id in wanted:
            doc = await self.get(entity_id, locale)
            if doc is not None:
                found.append(doc)
        if cache_key and self._state is not None and wanted:
            try:
                await self._state.set_state(
                    LIST_CACHE_SCOPE,
                    cache_key,
                    {"docs": found},
                    ttl=LIST_CACHE_TTL_S,
                )
                for entity_id in wanted:
                    tracker_key = f"tracker:{entity_id}:{locale}"
                    tracked = await self._state.get_state(LIST_CACHE_SCOPE, tracker_key) or {}
                    keys = sorted({*list(tracked.get("keys", [])), cache_key})
                    await self._state.set_state(
                        LIST_CACHE_SCOPE,
                        tracker_key,
                        {"keys": keys},
                        ttl=LIST_CACHE_TTL_S,
                    )
            except Exception:
                logger.debug("content list cache write failed (%s)", cache_key, exc_info=True)
        return found

    async def _invalidate_cache(self, entity_id: str, locale: str) -> None:
        """Drop the cached entry after a store (next read reloads it)."""
        if self._state is None:
            return
        try:
            await self._state.delete_state(CACHE_SCOPE, f"{entity_id}:{locale}")
        except Exception:
            logger.debug("content cache delete failed (%s)", entity_id, exc_info=True)

    async def _invalidate_list_caches(self, entity_id: str, locale: str) -> None:
        """Drop every cached guild list containing one entity after a store.

        The tracker set below ``tracker:<entity>:<locale>`` records
        each cache key the entity landed in at list time; a store of
        that entity deletes them, so a guild list never serves stale
        data past the next read. Tracker TTLs bound growth.
        """
        if self._state is None:
            return
        try:
            tracker_key = f"tracker:{entity_id}:{locale}"
            tracked = await self._state.get_state(LIST_CACHE_SCOPE, tracker_key) or {}
            for key in tracked.get("keys", []):
                await self._state.delete_state(LIST_CACHE_SCOPE, key)
            await self._state.delete_state(LIST_CACHE_SCOPE, tracker_key)
        except Exception:
            logger.debug("content list cache invalidation failed (%s)", entity_id, exc_info=True)

        """Drop the cached entry after a store (next read reloads it)."""
        if self._state is None:
            return
        try:
            await self._state.delete_state(CACHE_SCOPE, f"{entity_id}:{locale}")
        except Exception:
            logger.debug("content cache delete failed (%s)", entity_id, exc_info=True)
