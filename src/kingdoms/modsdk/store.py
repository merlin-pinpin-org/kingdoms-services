"""The modder SDK storage: one generic store, no Mongo in the mod.

An ``EntityStore`` backs one entity spec with a generic Mongo
collection; the mod (or its agent) never writes a query — the SDK
owns the ids (``<prefix>:<scope>:<slug>``), the upserts, the
list-by-scope and the state transitions. This is the whole
persistence surface the mods are allowed to touch.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from kingdoms.core.ids import slug_id
from kingdoms.modsdk.models import EntitySpec

logger = logging.getLogger("kingdoms.modsdk.store")


class EntityStore:
    """The generic per-entity Mongo store of the modder SDK."""

    def __init__(self, database: Any, spec: EntitySpec) -> None:
        """Bind the store to its Mongo database and entity spec."""
        self._database = database
        self._spec = spec
        self._collection = f"modsdk_{spec.key}"

    def entity_id(self, name: str, scope: str = "public") -> str:
        """Build the entity's stable id (``<prefix>:<game>:<scope>:<slug>``).

        Same convention as the platform's own entities
        (``map:aoe2:public:nomad``): the id carries the game, the
        visibility scope and the name slug — no hand-formatting.
        """
        game = self._spec.game_key or "core"
        return f"{self._spec.id_prefix}:{game}:{scope}:{slug_id(name)}"

    async def create(
        self,
        scope: str,
        name: str,
        fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create one entity in its scope; return the stored doc."""
        entity = {
            "_id": self.entity_id(name, scope),
            "kind": self._spec.key,
            "scope": scope,
            "name": name,
            "state": self._spec.initial_state,
            "created_at": int(time.time()),
            "updated_at": int(time.time()),
        }
        for key, value in (fields or {}).items():
            entity.setdefault(key, value)
        await self._database[self._collection].replace_one({"_id": entity["_id"]}, entity, upsert=True)
        return entity

    async def get(self, entity_id: str) -> dict[str, Any] | None:
        """Return one entity by id; None when unknown."""
        doc = await self._database[self._collection].find_one({"_id": entity_id})
        return dict(doc) if doc else None

    async def list(self, scope: str, include_archived: bool = False) -> list[dict[str, Any]]:
        """List a scope's entities, newest last; archived last when kept."""
        query: dict[str, Any] = {"scope": scope}
        if not include_archived:
            query["archived_at"] = None
        cursor = self._database[self._collection].find(query).sort("created_at", 1)
        return [dict(doc) async for doc in cursor]

    async def update(
        self,
        entity_id: str,
        changes: dict[str, Any],
        new_state: str = "",
    ) -> dict[str, Any] | None:
        """Merge ``changes`` into one entity; optionally move its state.

        The SDK's action pipeline persists through here — the mod
        returns its ``ActionResult`` and this lands it. Returns the
        updated doc (None when the entity vanished mid-flight).
        """
        doc = await self.get(entity_id)
        if doc is None:
            return None
        doc.update(changes)
        if new_state:
            doc["state"] = new_state
        doc["updated_at"] = int(time.time())
        await self._database[self._collection].replace_one({"_id": entity_id}, doc, upsert=True)
        return doc

    async def archive(self, entity_id: str) -> dict[str, Any] | None:
        """Archive one entity (reversible — nothing is ever deleted)."""
        return await self.update(entity_id, {"archived_at": int(time.time())})


__all__ = ["EntityStore"]
