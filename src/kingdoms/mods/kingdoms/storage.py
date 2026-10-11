"""Kingdoms mod persistence: a narrow async store over the season data.

The season collections are owned by the mod (ADR-0020 note on the
models): seasons, kingdoms and lords live in MongoDB behind this seam.
The Protocol keeps the domain testable in memory (the repo's testing
rule — no network in unit tests); ``MongoKingdomsStore`` is the one
production adapter, wrapping ``get_async_database`` exactly like the
logs and AoE2 seed adapters.
"""
from __future__ import annotations

from typing import Any, Protocol

SEASONS_COLLECTION = "kingdoms_seasons"
KINGDOMS_COLLECTION = "kingdoms_kingdoms"
LORDS_COLLECTION = "kingdoms_lords"
TERRITORIES_COLLECTION = "kingdoms_territories"
TECHNOLOGIES_COLLECTION = "kingdoms_technologies"
ATTACKS_COLLECTION = "kingdoms_attacks"
SHOWMATCH_COLLECTION = "kingdoms_showmatch"
SEASON_ARCHIVES_COLLECTION = "kingdoms_season_archives"


class KingdomsStore(Protocol):
    """The narrow async persistence seam of the kingdoms mod."""

    async def upsert_season(self, document: dict[str, Any]) -> None:
        """Insert or replace one season document by ``_id``."""
        ...

    async def find_seasons(self) -> list[dict[str, Any]]:
        """Return every season document (ascending by ``started_at``)."""
        ...

    async def upsert_kingdom(self, document: dict[str, Any]) -> None:
        """Insert or replace one kingdom document by ``_id``."""
        ...

    async def find_kingdoms(self) -> list[dict[str, Any]]:
        """Return every kingdom document of the current data set."""
        ...

    async def delete_kingdom(self, kingdom_id: str) -> None:
        """Drop one kingdom document by ``_id``."""
        ...

    async def upsert_lord(self, document: dict[str, Any]) -> None:
        """Insert or replace one lord document by ``_id``."""
        ...

    async def find_lords(self) -> list[dict[str, Any]]:
        """Return every lord document of the current data set."""
        ...

    async def delete_lord(self, lord_id: str) -> None:
        """Drop one lord document by ``_id``."""
        ...

    async def upsert_territory(self, document: dict[str, Any]) -> None:
        """Insert or replace one territory document by ``_id``."""
        ...

    async def find_territories(self) -> list[dict[str, Any]]:
        """Return every territory document of the current data set."""
        ...

    async def delete_territory(self, territory_id: str) -> None:
        """Drop one territory document by ``_id``."""
        ...

    async def upsert_attack(self, document: dict[str, Any]) -> None:
        """Insert or replace one attack document by ``_id``."""
        ...

    async def find_attacks(self) -> list[dict[str, Any]]:
        """Return every attack document of the current data set."""
        ...

    async def delete_attack(self, attack_id: str) -> None:
        """Drop one attack document by ``_id``."""
        ...

    async def upsert_technology(self, document: dict[str, Any]) -> None:
        """Insert or replace one technology document by ``_id``."""
        ...

    async def find_technologies(self) -> list[dict[str, Any]]:
        """Return every technology document of the current data set."""
        ...

    async def delete_technology(self, kingdom_id: str) -> None:
        """Drop one technology document by ``_id``."""
        ...

    async def upsert_showmatch(self, document: dict[str, Any]) -> None:
        """Insert or replace the ShowMatch document by ``_id``."""
        ...

    async def find_showmatches(self) -> list[dict[str, Any]]:
        """Return every ShowMatch document of the current data set."""
        ...

    async def wipe_season_data(self) -> None:
        """Reset the season data wholesale (D38): seasons, kingdoms, lords."""
        ...

    async def upsert_season_archive(self, document: dict[str, Any]) -> None:
        """Insert one season archive document (backup before a wipe)."""
        ...

    async def find_season_archives(self) -> list[dict[str, Any]]:
        """Return every archived season snapshot (ascending by date)."""
        ...


class MongoKingdomsStore:
    """Async MongoDB adapter for the KingdomsStore seam."""

    def __init__(self, database: Any) -> None:
        """Wrap an async MongoDB database (``get_async_database``)."""
        self._database = database

    async def upsert_season(self, document: dict[str, Any]) -> None:
        """Insert or replace one season document by ``_id``."""
        await self._database[SEASONS_COLLECTION].replace_one(
            {"_id": document["_id"]}, document, upsert=True
        )

    async def find_seasons(self) -> list[dict[str, Any]]:
        """Return every season document (ascending by ``started_at``)."""
        cursor = self._database[SEASONS_COLLECTION].find({}).sort("started_at", 1)
        return [dict(doc) async for doc in cursor]

    async def upsert_kingdom(self, document: dict[str, Any]) -> None:
        """Insert or replace one kingdom document by ``_id``."""
        await self._database[KINGDOMS_COLLECTION].replace_one(
            {"_id": document["_id"]}, document, upsert=True
        )

    async def find_kingdoms(self) -> list[dict[str, Any]]:
        """Return every kingdom document of the current data set."""
        cursor = self._database[KINGDOMS_COLLECTION].find({})
        return [dict(doc) async for doc in cursor]

    async def delete_kingdom(self, kingdom_id: str) -> None:
        """Drop one kingdom document by ``_id``."""
        await self._database[KINGDOMS_COLLECTION].delete_one({"_id": kingdom_id})

    async def upsert_lord(self, document: dict[str, Any]) -> None:
        """Insert or replace one lord document by ``_id``."""
        await self._database[LORDS_COLLECTION].replace_one(
            {"_id": document["_id"]}, document, upsert=True
        )

    async def find_lords(self) -> list[dict[str, Any]]:
        """Return every lord document of the current data set."""
        cursor = self._database[LORDS_COLLECTION].find({})
        return [dict(doc) async for doc in cursor]

    async def delete_lord(self, lord_id: str) -> None:
        """Drop one lord document by ``_id``."""
        await self._database[LORDS_COLLECTION].delete_one({"_id": lord_id})

    async def upsert_territory(self, document: dict[str, Any]) -> None:
        """Insert or replace one territory document by ``_id``."""
        await self._database[TERRITORIES_COLLECTION].replace_one(
            {"_id": document["_id"]}, document, upsert=True
        )

    async def find_territories(self) -> list[dict[str, Any]]:
        """Return every territory document of the current data set."""
        cursor = self._database[TERRITORIES_COLLECTION].find({})
        return [dict(doc) async for doc in cursor]

    async def delete_territory(self, territory_id: str) -> None:
        """Drop one territory document by ``_id``."""
        await self._database[TERRITORIES_COLLECTION].delete_one({"_id": territory_id})

    async def upsert_attack(self, document: dict[str, Any]) -> None:
        """Insert or replace one attack document by ``_id``."""
        await self._database[ATTACKS_COLLECTION].replace_one(
            {"_id": document["_id"]}, document, upsert=True
        )

    async def find_attacks(self) -> list[dict[str, Any]]:
        """Return every attack document of the current data set."""
        cursor = self._database[ATTACKS_COLLECTION].find({})
        return [dict(doc) async for doc in cursor]

    async def delete_attack(self, attack_id: str) -> None:
        """Drop one attack document by ``_id``."""
        await self._database[ATTACKS_COLLECTION].delete_one({"_id": attack_id})

    async def upsert_technology(self, document: dict[str, Any]) -> None:
        """Insert or replace one technology document by ``_id``."""
        await self._database[TECHNOLOGIES_COLLECTION].replace_one(
            {"_id": document["_id"]}, document, upsert=True
        )

    async def find_technologies(self) -> list[dict[str, Any]]:
        """Return every technology document of the current data set."""
        cursor = self._database[TECHNOLOGIES_COLLECTION].find({})
        return [dict(doc) async for doc in cursor]

    async def delete_technology(self, document_id: str) -> None:
        """Drop one technology document by ``_id``."""
        await self._database[TECHNOLOGIES_COLLECTION].delete_one({"_id": document_id})

    async def upsert_showmatch(self, document: dict[str, Any]) -> None:
        """Insert or replace the ShowMatch document by ``_id``."""
        await self._database[SHOWMATCH_COLLECTION].replace_one(
            {"_id": document["_id"]}, document, upsert=True
        )

    async def find_showmatches(self) -> list[dict[str, Any]]:
        """Return every ShowMatch document of the current data set."""
        cursor = self._database[SHOWMATCH_COLLECTION].find({})
        return [dict(doc) async for doc in cursor]

    async def wipe_season_data(self) -> None:
        """Reset the season data wholesale (D38).

        Every season-scoped collection is dropped: a new season never
        inherits anything from the previous one (reference §3.3).
        """
        for collection in (
            SEASONS_COLLECTION,
            KINGDOMS_COLLECTION,
            LORDS_COLLECTION,
            TERRITORIES_COLLECTION,
            TECHNOLOGIES_COLLECTION,
            ATTACKS_COLLECTION,
            SHOWMATCH_COLLECTION,
        ):
            await self._database[collection].delete_many({})

    async def upsert_season_archive(self, document: dict[str, Any]) -> None:
        """Insert one season archive document (never wiped by a reset)."""
        await self._database[SEASON_ARCHIVES_COLLECTION].insert_one(dict(document))

    async def find_season_archives(self) -> list[dict[str, Any]]:
        """Return every archived season snapshot (ascending by archived_at)."""
        cursor = self._database[SEASON_ARCHIVES_COLLECTION].find({}).sort("archived_at", 1)
        return [dict(doc) async for doc in cursor]
