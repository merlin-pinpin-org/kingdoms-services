"""Provider mapping service: each provider's own ids, remapped (core).

Every provider speaks its own dialect — aoe2techtree keys civs as
``Franks``, a match API may key them ``1`` or ``franks``; a patch can
renumber them. The catalog's stable ids never move; the provider's ids
can. This service holds the per-provider remapping tables (catalog
name -> provider id/name), persisted in Mongo (``provider_mappings``,
one document per provider) and read cache-aside through the
StateService (Redis) like the rest of the provider data.

Bot admins edit the mappings from the admin DM panel after a game
patch: the mapping is data, not code — a provider renumbering is a
mapping fix, never a deploy.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

logger = logging.getLogger("kingdoms.core.provider_mapping")

PROVIDER_MAPPINGS_COLLECTION = "provider_mappings"
CACHE_SCOPE = "provider_mappings"
CACHE_TTL_S = 600

MAPPING_KINDS = ("factions", "maps")


class ProviderMappingDatabase(Protocol):
    """Persistence seam for the per-provider mapping documents."""

    async def get_mapping(self, provider: str) -> dict[str, Any] | None:
        """Read one provider's mapping document; None when absent."""
        ...

    async def upsert_mapping(self, document: dict[str, Any]) -> None:
        """Insert or replace one mapping document by ``_id``."""
        ...


class ProviderMappingError(ValueError):
    """A mapping update is invalid (unknown kind, bad line format)."""


def parse_mapping_lines(lines: list[str]) -> dict[str, str]:
    """Parse ``catalog=provider`` lines into a mapping dict.

    Blank lines are skipped; a line without ``=`` raises loudly — a
    mapping fix must be explicit, never silently dropped.
    """
    mapping: dict[str, str] = {}
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if "=" not in line:
            raise ProviderMappingError(f"ligne de mapping invalide (attendu catalog=provider) : {line!r}")
        catalog, provider_id = line.split("=", 1)
        catalog = catalog.strip()
        provider_id = provider_id.strip()
        if not catalog or not provider_id:
            raise ProviderMappingError(f"ligne de mapping invalide (membre vide) : {line!r}")
        mapping[catalog] = provider_id
    return mapping


class ProviderMappingService:
    """Per-provider id remapping tables, cache-aside over Mongo."""

    def __init__(self, database: ProviderMappingDatabase, state: Any | None = None) -> None:
        """Store the persistence and optional Redis-cache seams."""
        self._db = database
        self._state = state

    async def get(self, provider: str) -> dict[str, Any]:
        """Return one provider's mapping document (default: empty)."""
        cached = await self._cache_get(provider)
        if cached is not None:
            return cached
        doc = await self._db.get_mapping(provider)
        if doc is None:
            doc = {"provider": provider, "factions": {}, "maps": {}}
        await self._cache_put(provider, doc)
        return dict(doc)

    async def resolve(self, provider: str, kind: str, catalog_name: str) -> str:
        """Resolve a catalog name through the provider's own id (fallback: identity)."""
        if kind not in MAPPING_KINDS:
            raise ProviderMappingError(f"kind de mapping inconnu : {kind!r}")
        doc = await self.get(provider)
        table = doc.get(kind) or {}
        return str(table.get(catalog_name, catalog_name))

    async def update_kind(self, provider: str, kind: str, mapping: dict[str, str]) -> None:
        """Replace one kind's mapping table for one provider."""
        if kind not in MAPPING_KINDS:
            raise ProviderMappingError(f"kind de mapping inconnu : {kind!r}")
        doc = await self.get(provider)
        doc[kind] = dict(mapping)
        await self._db.upsert_mapping(self._to_mongo(doc))
        await self._cache_invalidate(provider)

    async def _cache_get(self, provider: str) -> dict[str, Any] | None:
        if self._state is None:
            return None
        try:
            cached = await self._state.get_state(CACHE_SCOPE, provider)
            return dict(cached) if cached is not None else None
        except Exception:
            logger.debug("mapping cache read failed (%s)", provider, exc_info=True)
            return None

    async def _cache_put(self, provider: str, doc: dict[str, Any]) -> None:
        if self._state is None:
            return
        try:
            await self._state.set_state(CACHE_SCOPE, provider, doc, ttl=CACHE_TTL_S)
        except Exception:
            logger.debug("mapping cache write failed (%s)", provider, exc_info=True)

    async def _cache_invalidate(self, provider: str) -> None:
        if self._state is None:
            return
        try:
            await self._state.delete_state(CACHE_SCOPE, provider)
        except Exception:
            logger.debug("mapping cache delete failed (%s)", provider, exc_info=True)

    def _to_mongo(self, doc: dict[str, Any]) -> dict[str, Any]:
        return {"_id": f"provider:{doc['provider']}", **doc}
