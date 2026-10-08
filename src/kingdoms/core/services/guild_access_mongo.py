"""MongoDB adapter for the per-guild access documents (core)."""

from __future__ import annotations

from typing import Any

from kingdoms.core.services.guild_access import GUILD_ACCESS_COLLECTION


class MongoGuildAccessDatabase:
    """Async MongoDB persistence for ``GuildAccessService``."""

    def __init__(self, database: Any) -> None:
        """Wrap an async MongoDB database (``get_async_database``)."""
        self._database = database

    async def get_guild_access(self, guild_id: str) -> dict[str, Any] | None:
        """Read one guild's access document; None when absent."""
        doc = await self._database[GUILD_ACCESS_COLLECTION].find_one({"_id": f"guild:{guild_id}"})
        return dict(doc) if doc is not None else None

    async def upsert_guild_access(self, document: dict[str, Any]) -> None:
        """Insert or replace one access document by ``_id``."""
        doc = dict(document)
        doc["_id"] = f"guild:{doc['guild_id']}"
        await self._database[GUILD_ACCESS_COLLECTION].replace_one({"_id": doc["_id"]}, doc, upsert=True)

    async def list_guild_access(self) -> list[dict[str, Any]]:
        """List every guild's access document."""
        cursor = self._database[GUILD_ACCESS_COLLECTION].find({})
        return [dict(doc) async for doc in cursor]
