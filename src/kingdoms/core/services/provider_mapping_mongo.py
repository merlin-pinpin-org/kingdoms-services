"""MongoDB adapter for the per-provider mapping documents (core)."""

from __future__ import annotations

from typing import Any

from kingdoms.core.services.provider_mapping import PROVIDER_MAPPINGS_COLLECTION


class MongoProviderMappingDatabase:
    """Async MongoDB persistence for ``ProviderMappingService``."""

    def __init__(self, database: Any) -> None:
        """Wrap an async MongoDB database (``get_async_database``)."""
        self._database = database

    async def get_mapping(self, provider: str) -> dict[str, Any] | None:
        """Read one provider's mapping document; None when absent."""
        doc = await self._database[PROVIDER_MAPPINGS_COLLECTION].find_one({"_id": f"provider:{provider}"})
        return dict(doc) if doc is not None else None

    async def upsert_mapping(self, document: dict[str, Any]) -> None:
        """Insert or replace one mapping document by ``_id``."""
        doc = dict(document)
        doc["_id"] = f"provider:{doc['provider']}"
        await self._database[PROVIDER_MAPPINGS_COLLECTION].replace_one({"_id": doc["_id"]}, doc, upsert=True)
