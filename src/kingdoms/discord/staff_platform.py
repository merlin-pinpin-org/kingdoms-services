"""Discord wiring for the staff persistence (Mongo, no business logic).

Implements the :class:`StaffDatabase` seam the core
:class:`StaffService` depends on, with real infrastructure only.
"""
from __future__ import annotations

from typing import Any

from kingdoms.core.services.staff import STAFF_COLLECTION


class MongoStaffDatabase:
    """Async MongoDB persistence for the staff documents."""

    def __init__(self, database: Any) -> None:
        """Wrap an async MongoDB database (get_async_database)."""
        self._staff = database[STAFF_COLLECTION]

    async def upsert_staff(self, document: dict[str, Any]) -> None:
        """Insert or replace one staff document."""
        await self._staff.replace_one({"_id": document["_id"]}, document, upsert=True)

    async def find_staff(self, staff_id: str) -> dict[str, Any] | None:
        """Return one staff document; None when absent."""
        document = await self._staff.find_one({"_id": staff_id})
        return dict(document) if document else None

    async def find_mod_staff(self, guild_id: str, mod: str) -> list[dict[str, Any]]:
        """Every staff document of one (guild, mod)."""
        cursor = self._staff.find({"guild_id": guild_id, "mod": mod})
        return [document async for document in cursor]
