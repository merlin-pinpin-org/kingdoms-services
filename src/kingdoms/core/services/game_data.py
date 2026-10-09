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

from kingdoms.core.ids import slug_id
from kingdoms.core.models.game_data import (
    FactionModel,
    MapModel,
    MapPackModel,
    MapPoolActivationModel,
    MapPoolModel,
    RuleModel,
)

logger = logging.getLogger("kingdoms.core.game_data")

T = TypeVar("T")

MAPS_COLLECTION = "maps"
MAX_POOL_MAPS = 25
MAP_POOLS_COLLECTION = "map_pools"
MAP_PACKS_COLLECTION = "map_packs"
MAP_POOL_HISTORY_COLLECTION = "map_pool_history"
ADMIN_AUDIT_COLLECTION = "admin_audit"
FACTIONS_COLLECTION = "factions"
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

    async def find_active_maps(self, game_key: str, guild_id: str | None = None) -> list[dict[str, Any]]:
        """List the non-archived maps for a game, scoped for one guild.

        Scoped: the guild's own maps plus the global ones; unscoped:
        every map of the game.
        """
        ...

    async def find_game_keys(self) -> list[str]:
        """List the distinct game keys present in the maps catalog."""
        ...

    async def find_active_map_pools(self, game_key: str, guild_id: str | None = None) -> list[dict[str, Any]]:
        """List the non-archived map pools for a game, scoped for one guild.

        Scoped: the guild's own pools plus the public ones (owned by other
        guilds but shared); unscoped: every pool of the game.
        """
        ...

    async def find_active_factions(self, game_key: str, guild_id: str | None = None) -> list[dict[str, Any]]:
        """List the non-archived factions for a game, scoped like the maps."""
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


class PoolNotEditableError(ValueError):
    """The pool is locked (activated once, or received from another guild)."""


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
        self,
        game_key: str,
        name: str,
        filename: str,
        description: str = "",
        resource_url: str = "",
        owner_guild_id: str | None = None,
    ) -> MapModel:
        """Create a map; the name must be unique among non-archived maps.

        ``owner_guild_id`` scopes the map to one guild (guild-local
        enrichment); None is the global catalog (config seed and bot
        admins). Global entries sync into every guild's forum; a
        guild's entries stay in that guild's forum.
        """
        if await self._db.find_by_name(MAPS_COLLECTION, game_key, name) is not None:
            raise NameTakenError(f"map {name!r} already exists for game {game_key!r}")
        entry = MapModel(
            _id=f"map:{game_key}:{slug_id(name)}",
            game_key=game_key,
            name=name,
            filename=filename,
            description=description,
            resource_url=resource_url,
            owner_guild_id=owner_guild_id,
        )
        await self._db.upsert_entry(MAPS_COLLECTION, entry.to_mongo())
        await self._audit_record("map.create", {"game_key": game_key, "name": name})
        return entry

    async def get_map(self, entry_id: str) -> MapModel | None:
        """Return one map; None when unknown."""
        doc = await self._db.find_entry(MAPS_COLLECTION, entry_id)
        return MapModel.from_mongo(doc) if doc else None

    async def list_game_keys(self) -> list[str]:
        """List the game keys known to the maps catalog (generic)."""
        return await self._db.find_game_keys()

    async def list_maps(self, game_key: str, guild_id: str | None = None) -> list[MapModel]:
        """List the non-archived maps of a game, scoped for one guild.

        Scoped: the guild's own maps plus the global ones; unscoped:
        every map of the game (the admin views).
        """
        docs = await self._db.find_active_maps(game_key, guild_id=guild_id)
        return [MapModel.from_mongo(d) for d in docs]

    async def list_map_pools(self, game_key: str, guild_id: str | None = None) -> list[MapPoolModel]:
        """List the non-archived map pools of a game, scoped for one guild.

        Scoped: the guild's own pools plus the public ones (owned by other
        guilds but shared with everyone); unscoped: every pool of the game.
        """
        docs = await self._db.find_active_map_pools(game_key, guild_id=guild_id)
        return [MapPoolModel.from_mongo(d) for d in docs]

    async def update_map(
        self,
        entry_id: str,
        name: str | None = None,
        filename: str | None = None,
        description: str | None = None,
        resource_url: str | None = None,
    ) -> MapModel:
        """Update a map's editable fields (guild-owned maps only change).

        The id stays stable (it is the pools' and posts' reference), so
        only the descriptive fields move; the name check keeps the
        game's taken names unique.
        """
        entry = await self._require(MAPS_COLLECTION, entry_id, MapModel.from_mongo)
        if entry.archived_at is not None:
            raise ArchivedEntryError(f"map {entry_id!r} is archived")
        if name is not None and name != entry.name:
            taken = await self._db.find_by_name(MAPS_COLLECTION, entry.game_key, name)
            if taken is not None and taken["_id"] != entry_id:
                raise NameTakenError(f"map {name!r} already exists for game {entry.game_key!r}")
        updates: dict[str, Any] = {}
        if name is not None and name != entry.name:
            updates["name"] = name
        if filename is not None:
            updates["filename"] = filename
        if description is not None:
            updates["description"] = description
        if resource_url is not None:
            updates["resource_url"] = resource_url
        if not updates:
            return entry
        updated = entry.model_copy(update=updates)
        await self._db.upsert_entry(MAPS_COLLECTION, updated.to_mongo())
        await self._audit_record("map.update", {"entry_id": entry_id, "changes": updates})
        return updated

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
        owner_guild_id: str | None = None,
        is_public: bool = False,
    ) -> MapPoolModel:
        """Create a pool referencing maps and/or packs; at least one map resolved.

        The pool belongs to a guild (``owner_guild_id``) or is global
        (None); a public pool is shared with every guild while keeping
        its owner (#04fcb94c).
        """
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
        if len(map_ids) > MAX_POOL_MAPS:
            raise ValueError(f"a map pool holds at most {MAX_POOL_MAPS} maps (Discord list caps), got {len(map_ids)}")
        pool = MapPoolModel(
            _id=f"map_pool:{game_key}:{name}",
            game_key=game_key,
            name=name,
            description=description,
            map_ids=tuple(map_ids),
            map_pack_ids=tuple(map_pack_ids),
            owner_guild_id=owner_guild_id,
            is_public=is_public,
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

    async def update_map_pool(
        self,
        entry_id: str,
        name: str | None = None,
        map_ids: tuple[str, ...] | None = None,
        description: str | None = None,
        fav_quota: int | None = None,
        ban_quota: int | None = None,
    ) -> MapPoolModel:
        """Update a pool's name, maps, description and/or quotas (validated).

        All arguments are optional: only the provided ones change. New
        maps are validated against the catalog, the name against the
        game's taken names (excluding itself). Quotas cap the players'
        fav/ban preferences on ladders running this pool (#222); ``None``
        keeps the current value, use ``0`` to disable a kind explicitly.
        """
        pool = await self._require(MAP_POOLS_COLLECTION, entry_id, MapPoolModel.from_mongo)
        name, map_ids, description = self._guard_pool_edition(pool, name, map_ids, description, fav_quota, ban_quota)
        if name is not None and name != pool.name:
            taken = await self._db.find_by_name(MAP_POOLS_COLLECTION, pool.game_key, name)
            if taken is not None and taken["_id"] != entry_id:
                raise NameTakenError(f"map pool {name!r} already exists for game {pool.game_key!r}")
        if map_ids is not None:
            for map_id in map_ids:
                if await self._db.find_entry(MAPS_COLLECTION, map_id) is None:
                    raise ValueError(f"unknown map {map_id!r}")
            if len(map_ids) > MAX_POOL_MAPS:
                raise ValueError(
                    f"a map pool holds at most {MAX_POOL_MAPS} maps (Discord list caps), got {len(map_ids)}"
                )
        updates = self._pool_updates(
            pool,
            name=name,
            map_ids=map_ids,
            description=description,
            fav_quota=fav_quota,
            ban_quota=ban_quota,
        )
        if not updates:
            return pool
        updated = pool.model_copy(update=updates)
        await self._db.upsert_entry(MAP_POOLS_COLLECTION, updated.to_mongo())
        await self._audit_record(
            "map_pool.update",
            {"pool_id": entry_id, "changes": {k: list(v) if k == "map_ids" else v for k, v in updates.items()}},
        )
        return updated

    @staticmethod
    def _guard_pool_edition(
        pool: MapPoolModel,
        name: str | None,
        map_ids: tuple[str, ...] | None,
        description: str | None,
        fav_quota: int | None,
        ban_quota: int | None,
    ) -> tuple[str | None, tuple[str, ...] | None, str | None]:
        """Enforce the edition lock: a locked pool only moves its quotas.

        Composition changes (name, maps, description) are refused on a
        locked pool — duplicate it to edit; a quota-only update is the
        ladder's runtime setting and stays allowed.
        """
        if pool.edition_mode:
            return name, map_ids, description
        if fav_quota is None and ban_quota is None:
            raise PoolNotEditableError(f"map pool {pool.id!r} is locked (duplicate it to edit)")
        return None, None, None

    @staticmethod
    def _pool_updates(
        pool: MapPoolModel,
        name: str | None,
        map_ids: tuple[str, ...] | None,
        description: str | None,
        fav_quota: int | None,
        ban_quota: int | None,
    ) -> dict[str, Any]:
        """Build the pool's change set; only the provided fields change."""
        updates: dict[str, Any] = {}
        if name is not None and name != pool.name:
            updates["name"] = name
        if description is not None:
            updates["description"] = description
        if map_ids is not None:
            updates["map_ids"] = tuple(map_ids)
        if fav_quota is not None:
            updates["fav_quota"] = int(fav_quota)
        if ban_quota is not None:
            updates["ban_quota"] = int(ban_quota)
        return updates

    async def set_map_forum_message(self, entry_id: str, forum_message_id: str) -> MapModel:
        """Record the map's forum post id (the map-message link)."""
        entry = await self._require(MAPS_COLLECTION, entry_id, MapModel.from_mongo)
        updated = entry.model_copy(update={"forum_message_id": forum_message_id})
        await self._db.upsert_entry(MAPS_COLLECTION, updated.to_mongo())
        return updated

    async def duplicate_map_pool(self, entry_id: str, new_name: str, owner_guild_id: str | None = None) -> MapPoolModel:
        """Duplicate a pool under a new name (a new stable id).

        The copy starts editable (a duplicate exists to be modified); the
        duplicating guild owns it, whatever the source's owner.
        """
        source = await self._require(MAP_POOLS_COLLECTION, entry_id, MapPoolModel.from_mongo)
        return await self.create_map_pool(
            source.game_key,
            new_name,
            map_ids=source.map_ids,
            map_pack_ids=source.map_pack_ids,
            description=source.description,
            owner_guild_id=owner_guild_id,
            is_public=False,
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
        if not pool.edition_mode or not pool.ever_activated:
            locked = pool.model_copy(update={"edition_mode": False, "ever_activated": True})
            await self._db.upsert_entry(MAP_POOLS_COLLECTION, locked.to_mongo())
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

    async def set_map_pool_public(
        self, entry_id: str, is_public: bool, owner_guild_id: str | None = None
    ) -> MapPoolModel:
        """Share a pool with every guild (or make it private again).

        The pool keeps its owner; making it public shares it with every
        guild's pool pickers and forums (#04fcb94c).
        """
        entry = await self._require(MAP_POOLS_COLLECTION, entry_id, MapPoolModel.from_mongo)
        updates: dict[str, Any] = {"is_public": is_public}
        if owner_guild_id is not None:
            updates["owner_guild_id"] = owner_guild_id
        updated = entry.model_copy(update=updates)
        await self._db.upsert_entry(MAP_POOLS_COLLECTION, updated.to_mongo())
        await self._audit_record("map_pool.visibility", {"entry_id": entry_id, "is_public": is_public})
        return updated

    async def send_map_pool_to_guild(self, entry_id: str, target_guild_id: str, new_name: str) -> MapPoolModel:
        """Send one pool to another guild: the received copy is locked.

        The receiving guild gets its own copy (own stable id, own forum
        post) that it can use and activate but never modify: the copy is
        created with ``edition_mode=False``.
        """
        copy = await self.duplicate_map_pool(entry_id, new_name, owner_guild_id=target_guild_id)
        locked = copy.model_copy(update={"edition_mode": False})
        await self._db.upsert_entry(MAP_POOLS_COLLECTION, locked.to_mongo())
        await self._audit_record(
            "map_pool.sent",
            {"pool_id": entry_id, "target_guild_id": target_guild_id, "copy_id": locked.id},
        )
        return locked

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

    async def create_faction(
        self,
        game_key: str,
        name: str,
        faction_key: str = "",
        description: str = "",
        resource_url: str = "",
        owner_guild_id: str | None = None,
    ) -> FactionModel:
        """Create a faction entry; name unique among non-archived."""
        if await self._db.find_by_name(FACTIONS_COLLECTION, game_key, name) is not None:
            raise NameTakenError(f"faction {name!r} already exists for game {game_key!r}")
        entry = FactionModel(
            _id=f"faction:{game_key}:{slug_id(name)}",
            game_key=game_key,
            name=name,
            faction_key=faction_key,
            description=description,
            resource_url=resource_url,
            owner_guild_id=owner_guild_id,
        )
        await self._db.upsert_entry(FACTIONS_COLLECTION, entry.to_mongo())
        await self._audit_record("faction.create", {"game_key": game_key, "name": name})
        return entry

    async def get_faction(self, entry_id: str) -> FactionModel | None:
        """Return one faction; None when unknown."""
        doc = await self._db.find_entry(FACTIONS_COLLECTION, entry_id)
        return FactionModel.from_mongo(doc) if doc else None

    async def list_factions(self, game_key: str, guild_id: str | None = None) -> list[FactionModel]:
        """List the non-archived factions of a game, scoped for one guild.

        Scoped: the guild's own factions plus the global ones; unscoped:
        every faction of the game (the admin views).
        """
        docs = await self._db.find_active_factions(game_key, guild_id=guild_id)
        return [FactionModel.from_mongo(d) for d in docs]

    async def archive_faction(self, entry_id: str) -> FactionModel:
        """Archive a faction (archival-only delete)."""
        entry = await self._require(FACTIONS_COLLECTION, entry_id, FactionModel.from_mongo)
        if entry.archived_at is not None:
            raise ArchivedEntryError(f"faction {entry_id!r} is already archived")
        entry = entry.model_copy(update={"archived_at": _now_ms()})
        await self._db.upsert_entry(FACTIONS_COLLECTION, entry.to_mongo())
        await self._audit_record("faction.archive", {"entry_id": entry_id})
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
            _id=f"rule:{game_key}:{slug_id(name)}",
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

        Sections ``maps``, ``factions`` (YAML section ``civs``), ``rules``: each a list of entries with
        a ``name`` plus optional fields. Entries already present (same id)
        are skipped, so seeding is safe to re-run.
        """
        counts = {"maps": 0, "factions": 0, "rules": 0}
        for spec in data.get("maps", []) or []:
            if await self.get_map(f"map:{game_key}:{slug_id(spec['name'])}") is not None:
                continue
            await self.create_map(
                game_key,
                spec["name"],
                filename=spec.get("filename", ""),
                description=spec.get("description", ""),
                resource_url=spec.get("resource_url", ""),
            )
            counts["maps"] += 1
        for spec in data.get("factions", data.get("civs", [])) or []:
            if await self.get_faction(f"faction:{game_key}:{slug_id(spec['name'])}") is not None:
                continue
            await self.create_faction(
                game_key,
                spec["name"],
                faction_key=spec.get("faction_key", ""),
                description=spec.get("description", ""),
                resource_url=spec.get("resource_url", ""),
            )
            counts["factions"] += 1
        for spec in data.get("rules", []) or []:
            if await self.get_rule(f"rule:{game_key}:{slug_id(spec['name'])}") is not None:
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
