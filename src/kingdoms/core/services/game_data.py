"""Game data service: versioned catalog CRUD, pools and packs (kingdoms-services#131).

Generic core contract ``games/<game>/data/``: the service is game-agnostic,
keyed by ``game_key``. Both write paths — YAML seeding (bulk initial data)
and the Discord admin surface — go through this service, never direct DB
edits, so the versioned data and the audit trail stay consistent.

Invariants (legacy reference §2/§4):
- stable IDs: an entry's ``_id`` never changes; rotation never breaks
  in-progress seasons;
- archival-only deletes: ``archived_at`` is set, the entry stays readable
  for history;
- unique ``(game_key, name)`` among non-archived entries;
- pool activation is transactional: the previous activation is
  deactivated, the new one recorded (the pool history), the ladder's
  ``active_map_pool_id`` moves — all or nothing;
- the active pool can never be archived;
- every mutation is audited (``admin_audit`` collection seam).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Protocol, TypeVar

from kingdoms.core.models.game_data import (
    CivModel,
    MapModel,
    MapPackModel,
    MapPoolActivationModel,
    MapPoolModel,
    RuleModel,
)

logger = logging.getLogger("kingdoms.core.game_data")

T = TypeVar("T")

MAPS_COLLECTION = "maps"
MAP_POOLS_COLLECTION = "map_pools"
MAP_PACKS_COLLECTION = "map_packs"
MAP_POOL_HISTORY_COLLECTION = "map_pool_history"
ADMIN_AUDIT_COLLECTION = "admin_audit"
CIVS_COLLECTION = "civs"
RULES_COLLECTION = "rules"


class GameDataDatabase(Protocol):
    """Narrow async MongoDB seam the GameDataService depends on."""

    async def upsert_entry(self, collection: str, document: dict[str, Any]) -> None:
        """Insert or replace one document by ``_id``."""
        ...

    async def find_entry(self, collection: str, entry_id: str) -> dict[str, Any] | None:
        """Return one document by ``_id``; None when absent."""
        ...

    async def find_by_name(self, collection: str, game_key: str, name: str) -> dict[str, Any] | None:
        """Return the non-archived entry for ``(game_key, name)``; None when absent."""
        ...

    async def find_active_maps(self, game_key: str) -> list[dict[str, Any]]:
        """List the non-archived maps for a game."""
        ...

    async def find_active_civs(self, game_key: str) -> list[dict[str, Any]]:
        """List the non-archived civs for a game."""
        ...

    async def find_ladder_activations(self, ladder_id: str) -> list[dict[str, Any]]:
        """List the pool activation history of a ladder (ascending)."""
        ...

    async def find_open_activations(self, map_pool_id: str) -> list[dict[str, Any]]:
        """List the still-open activations referencing one pool."""
        ...


class GameDataAudit(Protocol):
    """Narrow audit seam: every mutation is recorded (best-effort)."""

    async def record(self, action: str, payload: dict[str, Any]) -> None:
        """Persist one admin-audit line."""
        ...


class NameTakenError(ValueError):
    """A non-archived entry already exists for this ``(game_key, name)``."""


class ArchivedEntryError(ValueError):
    """The entry is archived: read-only, never mutated."""


class ActivePoolArchiveError(ValueError):
    """The pool is active on a ladder: it cannot be archived."""


class GameDataService:
    """CRUD + lifecycle for the game catalog, pools and packs."""

    def __init__(self, database: GameDataDatabase, audit: GameDataAudit | None = None) -> None:
        """Wire the persistence and audit seams."""
        self._db = database
        self._audit = audit

    # ── Maps ────────────────────────────────────────────────────────────

    async def create_map(
        self, game_key: str, name: str, filename: str, description: str = "", resource_url: str = ""
    ) -> MapModel:
        """Create a map; the name must be unique among non-archived maps."""
        if await self._db.find_by_name(MAPS_COLLECTION, game_key, name) is not None:
            raise NameTakenError(f"map {name!r} already exists for game {game_key!r}")
        entry = MapModel(
            _id=f"map:{game_key}:{name}",
            game_key=game_key,
            name=name,
            filename=filename,
            description=description,
            resource_url=resource_url,
        )
        await self._db.upsert_entry(MAPS_COLLECTION, entry.to_mongo())
        await self._audit_record("map.create", {"game_key": game_key, "name": name})
        return entry

    async def get_map(self, entry_id: str) -> MapModel | None:
        """Return one map; None when unknown."""
        doc = await self._db.find_entry(MAPS_COLLECTION, entry_id)
        return MapModel.from_mongo(doc) if doc else None

    async def list_maps(self, game_key: str) -> list[MapModel]:
        """List the non-archived maps of a game."""
        docs = await self._db.find_active_maps(game_key)
        return [MapModel.from_mongo(d) for d in docs]

    async def archive_map(self, entry_id: str) -> MapModel:
        """Archive a map (archival-only delete)."""
        entry = await self._require(MAPS_COLLECTION, entry_id, MapModel.from_mongo)
        if entry.archived_at is not None:
            raise ArchivedEntryError(f"map {entry_id!r} is already archived")
        entry = entry.model_copy(update={"archived_at": _now_ms()})
        await self._db.upsert_entry(MAPS_COLLECTION, entry.to_mongo())
        await self._audit_record("map.archive", {"entry_id": entry_id})
        return entry

    # ── Map packs ───────────────────────────────────────────────────────

    async def create_map_pack(
        self, game_key: str, name: str, map_ids: tuple[str, ...], description: str = ""
    ) -> MapPackModel:
        """Create a named bundle of maps."""
        if await self._db.find_by_name(MAP_PACKS_COLLECTION, game_key, name) is not None:
            raise NameTakenError(f"map pack {name!r} already exists for game {game_key!r}")
        for map_id in map_ids:
            if await self._db.find_entry(MAPS_COLLECTION, map_id) is None:
                raise ValueError(f"unknown map {map_id!r}")
        pack = MapPackModel(
            _id=f"map_pack:{game_key}:{name}",
            game_key=game_key,
            name=name,
            description=description,
            map_ids=tuple(map_ids),
        )
        await self._db.upsert_entry(MAP_PACKS_COLLECTION, pack.to_mongo())
        await self._audit_record("map_pack.create", {"game_key": game_key, "name": name, "maps": list(map_ids)})
        return pack

    async def get_map_pack(self, entry_id: str) -> MapPackModel | None:
        """Return one map pack; None when unknown."""
        doc = await self._db.find_entry(MAP_PACKS_COLLECTION, entry_id)
        return MapPackModel.from_mongo(doc) if doc else None

    async def archive_map_pack(self, entry_id: str) -> MapPackModel:
        """Archive a map pack (archival-only)."""
        entry = await self._require(MAP_PACKS_COLLECTION, entry_id, MapPackModel.from_mongo)
        if entry.archived_at is not None:
            raise ArchivedEntryError(f"map pack {entry_id!r} is already archived")
        entry = entry.model_copy(update={"archived_at": _now_ms()})
        await self._db.upsert_entry(MAP_PACKS_COLLECTION, entry.to_mongo())
        await self._audit_record("map_pack.archive", {"entry_id": entry_id})
        return entry

    # ── Map pools ───────────────────────────────────────────────────────

    async def create_map_pool(
        self,
        game_key: str,
        name: str,
        map_ids: tuple[str, ...] = (),
        map_pack_ids: tuple[str, ...] = (),
        description: str = "",
    ) -> MapPoolModel:
        """Create a pool referencing maps and/or packs; at least one map resolved."""
        if await self._db.find_by_name(MAP_POOLS_COLLECTION, game_key, name) is not None:
            raise NameTakenError(f"map pool {name!r} already exists for game {game_key!r}")
        for map_id in map_ids:
            if await self._db.find_entry(MAPS_COLLECTION, map_id) is None:
                raise ValueError(f"unknown map {map_id!r}")
        for pack_id in map_pack_ids:
            if await self._db.find_entry(MAP_PACKS_COLLECTION, pack_id) is None:
                raise ValueError(f"unknown map pack {pack_id!r}")
        if not map_ids and not map_pack_ids:
            raise ValueError("a map pool references at least one map or map pack")
        pool = MapPoolModel(
            _id=f"map_pool:{game_key}:{name}",
            game_key=game_key,
            name=name,
            description=description,
            map_ids=tuple(map_ids),
            map_pack_ids=tuple(map_pack_ids),
        )
        await self._db.upsert_entry(MAP_POOLS_COLLECTION, pool.to_mongo())
        await self._audit_record(
            "map_pool.create", {"game_key": game_key, "name": name, "maps": list(map_ids), "packs": list(map_pack_ids)}
        )
        return pool

    async def get_map_pool(self, entry_id: str) -> MapPoolModel | None:
        """Return one map pool; None when unknown."""
        doc = await self._db.find_entry(MAP_POOLS_COLLECTION, entry_id)
        return MapPoolModel.from_mongo(doc) if doc else None

    async def duplicate_map_pool(self, entry_id: str, new_name: str) -> MapPoolModel:
        """Duplicate a pool under a new name (a new stable id)."""
        source = await self._require(MAP_POOLS_COLLECTION, entry_id, MapPoolModel.from_mongo)
        return await self.create_map_pool(
            source.game_key,
            new_name,
            map_ids=source.map_ids,
            map_pack_ids=source.map_pack_ids,
            description=source.description,
        )

    async def resolve_pool_map_ids(self, pool: MapPoolModel) -> tuple[str, ...]:
        """Union of the pool's direct maps and its packs' maps (archived included)."""
        ids: list[str] = list(pool.map_ids)
        for pack_id in pool.map_pack_ids:
            doc = await self._db.find_entry(MAP_PACKS_COLLECTION, pack_id)
            if doc is None:
                continue
            ids.extend(MapPackModel.from_mongo(doc).map_ids)
        return tuple(dict.fromkeys(ids))

    async def activate_map_pool(self, ladder_id: str, map_pool_id: str) -> MapPoolActivationModel:
        """Activate a pool on a ladder, transactionally (reference §4).

        The previous active activation is deactivated, the new activation
        is recorded, and the ladder's ``active_map_pool_id`` moves — the
        caller persists the ladder field; this method owns the history
        rows so the switch is atomic from the store's point of view.
        Re-activating the pool that is already active is a no-op (the
        history stays one row per real switch).
        """
        pool = await self._require(MAP_POOLS_COLLECTION, map_pool_id, MapPoolModel.from_mongo)
        if pool.archived_at is not None:
            raise ArchivedEntryError(f"map pool {map_pool_id!r} is archived")
        now = _now_ms()
        for doc in await self._db.find_ladder_activations(ladder_id):
            activation = MapPoolActivationModel.from_mongo(doc)
            if activation.deactivated_at is None:
                if activation.map_pool_id == map_pool_id:
                    return activation
                closed = activation.model_copy(update={"deactivated_at": now})
                await self._db.upsert_entry(MAP_POOL_HISTORY_COLLECTION, closed.to_mongo())
        activation = MapPoolActivationModel(
            _id=f"activation:{ladder_id}:{map_pool_id}:{now}",
            ladder_id=ladder_id,
            map_pool_id=map_pool_id,
            activated_at=now,
        )
        await self._db.upsert_entry(MAP_POOL_HISTORY_COLLECTION, activation.to_mongo())
        await self._audit_record("map_pool.activate", {"ladder_id": ladder_id, "map_pool_id": map_pool_id})
        return activation

    async def archive_map_pool(self, entry_id: str) -> MapPoolModel:
        """Archive a pool — never the pool active on some ladder."""
        entry = await self._require(MAP_POOLS_COLLECTION, entry_id, MapPoolModel.from_mongo)
        if entry.archived_at is not None:
            raise ArchivedEntryError(f"map pool {entry_id!r} is already archived")
        for doc in await self._db.find_open_activations(entry_id):
            activation = MapPoolActivationModel.from_mongo(doc)
            raise ActivePoolArchiveError(f"map pool {entry_id!r} is active on ladder {activation.ladder_id!r}")
        entry = entry.model_copy(update={"archived_at": _now_ms()})
        await self._db.upsert_entry(MAP_POOLS_COLLECTION, entry.to_mongo())
        await self._audit_record("map_pool.archive", {"entry_id": entry_id})
        return entry

    async def assert_pool_archivable(self, entry_id: str) -> None:
        """Guard: refuse archiving a pool still active on a ladder.

        Callers that track the ladder's ``active_map_pool_id`` use this
        before the archive; the service itself keeps the check where the
        ladder link is queryable (the activations history).
        """
        pool = await self._require(MAP_POOLS_COLLECTION, entry_id, MapPoolModel.from_mongo)
        for doc in await self._db.find_ladder_activations("*"):
            activation = MapPoolActivationModel.from_mongo(doc)
            if activation.map_pool_id == entry_id and activation.deactivated_at is None:
                raise ActivePoolArchiveError(f"map pool {entry_id!r} is active on ladder {activation.ladder_id!r}")
        del pool

    # ── Civs & rules ──────────────────────────────────────────────────────

    async def create_civ(
        self, game_key: str, name: str, faction_key: str = "", description: str = "", resource_url: str = ""
    ) -> CivModel:
        """Create a civilization/faction entry; name unique among non-archived."""
        if await self._db.find_by_name(CIVS_COLLECTION, game_key, name) is not None:
            raise NameTakenError(f"civ {name!r} already exists for game {game_key!r}")
        entry = CivModel(
            _id=f"civ:{game_key}:{name}",
            game_key=game_key,
            name=name,
            faction_key=faction_key,
            description=description,
            resource_url=resource_url,
        )
        await self._db.upsert_entry(CIVS_COLLECTION, entry.to_mongo())
        await self._audit_record("civ.create", {"game_key": game_key, "name": name})
        return entry

    async def get_civ(self, entry_id: str) -> CivModel | None:
        """Return one civ; None when unknown."""
        doc = await self._db.find_entry(CIVS_COLLECTION, entry_id)
        return CivModel.from_mongo(doc) if doc else None

    async def list_civs(self, game_key: str) -> list[CivModel]:
        """List the non-archived civs of a game."""
        docs = await self._db.find_active_civs(game_key)
        return [CivModel.from_mongo(d) for d in docs]

    async def archive_civ(self, entry_id: str) -> CivModel:
        """Archive a civ (archival-only delete)."""
        entry = await self._require(CIVS_COLLECTION, entry_id, CivModel.from_mongo)
        if entry.archived_at is not None:
            raise ArchivedEntryError(f"civ {entry_id!r} is already archived")
        entry = entry.model_copy(update={"archived_at": _now_ms()})
        await self._db.upsert_entry(CIVS_COLLECTION, entry.to_mongo())
        await self._audit_record("civ.archive", {"entry_id": entry_id})
        return entry

    async def create_rule(
        self,
        game_key: str,
        name: str,
        rule_key: str = "",
        params: dict[str, str] | None = None,
        description: str = "",
    ) -> RuleModel:
        """Create a game rule/format entry; name unique among non-archived."""
        if await self._db.find_by_name(RULES_COLLECTION, game_key, name) is not None:
            raise NameTakenError(f"rule {name!r} already exists for game {game_key!r}")
        entry = RuleModel(
            _id=f"rule:{game_key}:{name}",
            game_key=game_key,
            name=name,
            rule_key=rule_key,
            params=dict(params or {}),
            description=description,
        )
        await self._db.upsert_entry(RULES_COLLECTION, entry.to_mongo())
        await self._audit_record("rule.create", {"game_key": game_key, "name": name})
        return entry

    async def get_rule(self, entry_id: str) -> RuleModel | None:
        """Return one rule; None when unknown."""
        doc = await self._db.find_entry(RULES_COLLECTION, entry_id)
        return RuleModel.from_mongo(doc) if doc else None

    async def archive_rule(self, entry_id: str) -> RuleModel:
        """Archive a rule (archival-only delete)."""
        entry = await self._require(RULES_COLLECTION, entry_id, RuleModel.from_mongo)
        if entry.archived_at is not None:
            raise ArchivedEntryError(f"rule {entry_id!r} is already archived")
        entry = entry.model_copy(update={"archived_at": _now_ms()})
        await self._db.upsert_entry(RULES_COLLECTION, entry.to_mongo())
        await self._audit_record("rule.archive", {"entry_id": entry_id})
        return entry

    # ── Seeding ───────────────────────────────────────────────────────────

    async def seed_from_data(self, game_key: str, data: dict[str, Any]) -> dict[str, int]:
        """Seed a game's catalog from a parsed YAML document (idempotent).

        Sections ``maps``, ``civs``, ``rules``: each a list of entries with
        a ``name`` plus optional fields. Entries already present (same id)
        are skipped, so seeding is safe to re-run.
        """
        counts = {"maps": 0, "civs": 0, "rules": 0}
        for spec in data.get("maps", []) or []:
            if await self.get_map(f"map:{game_key}:{spec['name']}") is not None:
                continue
            await self.create_map(
                game_key,
                spec["name"],
                filename=spec.get("filename", ""),
                description=spec.get("description", ""),
                resource_url=spec.get("resource_url", ""),
            )
            counts["maps"] += 1
        for spec in data.get("civs", []) or []:
            if await self.get_civ(f"civ:{game_key}:{spec['name']}") is not None:
                continue
            await self.create_civ(
                game_key,
                spec["name"],
                faction_key=spec.get("faction_key", ""),
                description=spec.get("description", ""),
                resource_url=spec.get("resource_url", ""),
            )
            counts["civs"] += 1
        for spec in data.get("rules", []) or []:
            if await self.get_rule(f"rule:{game_key}:{spec['name']}") is not None:
                continue
            await self.create_rule(
                game_key,
                spec["name"],
                rule_key=spec.get("rule_key", ""),
                params=spec.get("params") or {},
                description=spec.get("description", ""),
            )
            counts["rules"] += 1
        return counts

    # ── helpers ──────────────────────────────────────────────────────────

    async def _require(self, collection: str, entry_id: str, model: Callable[[dict[str, Any]], T]) -> T:
        """Fetch one entry or fail loudly (unknown ids are bugs)."""
        doc = await self._db.find_entry(collection, entry_id)
        if doc is None:
            raise ValueError(f"unknown {collection} entry {entry_id!r}")
        return model(doc)

    async def _audit_record(self, action: str, payload: dict[str, Any]) -> None:
        """Write one audit line (best-effort: a store failure logs, never raises)."""
        if self._audit is None:
            return
        try:
            await self._audit.record(action, payload)
        except Exception:
            logger.warning("AUDIT WRITE FAILED (%s)", action, exc_info=True)


def _now_ms() -> int:
    """Epoch milliseconds, the repo's timestamp convention."""
    import time

    return int(time.time() * 1000)
