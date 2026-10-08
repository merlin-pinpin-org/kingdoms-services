"""AoE2 seeding: MongoDB adapters and idempotent seed orchestration (#137).

Loads ``config/games/aoe2/seed.yaml`` and drives the real services
(GameData, Ladder, Season) through a single async MongoDB adapter, so
both write paths of reference §2/§4 stay consistent: seeding goes through
the services, never direct DB edits, and remains safe to re-run.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import yaml

from kingdoms.core.services.game_data import GameDataService
from kingdoms.core.services.game_keys import validate_game_key

SEED_PATH = Path("config/games/aoe2/seed.yaml")
DAY_MS = 86_400_000


def load_seed_data(path: Path = SEED_PATH) -> dict[str, Any]:
    """Parse the AoE2 seed YAML document."""
    with open(path) as fh:
        data = yaml.safe_load(fh)
    return dict(data)


class MongoAoE2Database:
    """Async MongoDB adapter for the GameData, Season and Ladder seams."""

    def __init__(self, database: Any) -> None:
        """Wrap an async MongoDB database (``get_async_database``)."""
        self._database = database

    async def upsert_entry(self, collection: str, document: dict[str, Any]) -> None:
        """Insert or replace one document by ``_id``."""
        await self._database[collection].replace_one({"_id": document["_id"]}, document, upsert=True)

    async def find_entry(self, collection: str, entry_id: str) -> dict[str, Any] | None:
        """Return one document by ``_id``; None when absent."""
        doc = await self._database[collection].find_one({"_id": entry_id})
        return doc if doc is None else dict(doc)

    async def find_by_name(self, collection: str, game_key: str, name: str) -> dict[str, Any] | None:
        """Return the non-archived entry for ``(game_key, name)``."""
        doc = await self._database[collection].find_one({"game_key": game_key, "name": name, "archived_at": None})
        return doc if doc is None else dict(doc)

    async def find_active_maps(self, game_key: str, guild_id: str | None = None) -> list[dict[str, Any]]:
        """List the non-archived maps for a game, scoped for one guild.

        Scoped: the guild's own maps plus the global ones (config seed,
        bot admins); unscoped: every map of the game.
        """
        query: dict[str, Any] = {"game_key": game_key, "archived_at": None}
        if guild_id is not None:
            query["$or"] = [{"owner_guild_id": guild_id}, {"owner_guild_id": None}]
        cursor = self._database[collection_name("maps")].find(query)
        return [doc async for doc in cursor]

    async def find_game_keys(self) -> list[str]:
        """List the distinct game keys present in the maps catalog."""
        keys = await self._database[collection_name("maps")].distinct("game_key")
        return [str(k) for k in keys if k]

    async def find_active_factions(self, game_key: str, guild_id: str | None = None) -> list[dict[str, Any]]:
        """List the non-archived civs for a game, scoped like the maps."""
        query: dict[str, Any] = {"game_key": game_key, "archived_at": None}
        if guild_id is not None:
            query["$or"] = [{"owner_guild_id": guild_id}, {"owner_guild_id": None}]
        cursor = self._database[collection_name("civs")].find(query)
        return [doc async for doc in cursor]

    async def find_active_map_pools(self, game_key: str, guild_id: str | None = None) -> list[dict[str, Any]]:
        """List the non-archived map pools for a game, scoped for one guild.

        Scoped: the guild's own pools plus the public ones (owned by other
        guilds but shared); unscoped: every pool of the game.
        """
        query: dict[str, Any] = {"game_key": game_key, "archived_at": None}
        if guild_id is not None:
            query["$or"] = [{"owner_guild_id": guild_id}, {"owner_guild_id": None}, {"is_public": True}]
        cursor = self._database[collection_name("map_pools")].find(query)
        return [doc async for doc in cursor]

    async def find_ladder_activations(self, ladder_id: str) -> list[dict[str, Any]]:
        """List the pool activation history of a ladder (ascending)."""
        cursor = (
            self._database[collection_name("map_pool_history")].find({"ladder_id": ladder_id}).sort("activated_at", 1)
        )
        return [doc async for doc in cursor]

    async def find_open_activations(self, map_pool_id: str) -> list[dict[str, Any]]:
        """List the still-open activations referencing one pool."""
        return [
            doc
            async for doc in self._database[collection_name("map_pool_history")].find(
                {"map_pool_id": map_pool_id, "deactivated_at": None}
            )
        ]

    # ── Season seam ──────────────────────────────────────────────────────

    async def upsert_season(self, document: dict[str, Any]) -> None:
        """Insert or replace one season document by ``_id``."""
        await self.upsert_entry(collection_name("seasons"), document)

    async def find_season(self, season_id: str) -> dict[str, Any] | None:
        """Return one season document; None when absent."""
        return await self.find_entry(collection_name("seasons"), season_id)

    async def find_ladder_seasons(self, ladder_id: str) -> list[dict[str, Any]]:
        """List the seasons of a ladder (ascending by start)."""
        cursor = self._database[collection_name("seasons")].find({"ladder_id": ladder_id}).sort("start_at", 1)
        return [doc async for doc in cursor]

    async def find_active_season(self, ladder_id: str) -> dict[str, Any] | None:
        """Return the ladder's active season; None when none."""
        doc = await self._database[collection_name("seasons")].find_one({"ladder_id": ladder_id, "state": "active"})
        return doc if doc is None else dict(doc)

    # ── Ladder seam ──────────────────────────────────────────────────────

    async def find_ladder_by_owner(self, owner_ref: str, game_key: str) -> dict[str, Any] | None:
        """Return the ladder of one owner for a game; None when absent."""
        doc = await self._database[collection_name("ladders")].find_one({"owner_ref": owner_ref, "game_key": game_key})
        return doc if doc is None else dict(doc)

    async def delete_entry(self, collection: str, entry_id: str) -> bool:
        """Delete one document by ``_id``; True when one was removed."""
        result = await self._database[collection].delete_one({"_id": entry_id})
        return bool(result.deleted_count > 0)

    async def find_player(self, ladder_id: str, user_id: str) -> dict[str, Any] | None:
        """Return one player document; None when absent."""
        return await self.find_entry(collection_name("players"), f"player:{ladder_id}:{user_id}")

    async def find_queued_players(self, ladder_id: str) -> list[dict[str, Any]]:
        """List the players currently queued on a ladder."""
        cursor = (
            self._database[collection_name("players")]
            .find({"ladder_id": ladder_id, "queued_at": {"$ne": None}})
            .sort("queued_at", 1)
        )
        return [doc async for doc in cursor]

    async def find_ladder_players(self, ladder_id: str) -> list[dict[str, Any]]:
        """List every registered player of a ladder."""
        cursor = self._database[collection_name("players")].find({"ladder_id": ladder_id})
        return [doc async for doc in cursor]

    async def find_active_match(self, ladder_id: str, user_id: str) -> dict[str, Any] | None:
        """Return the user's live match on the ladder; None when free."""
        live = ["lobby_open", "lobby_closed", "game_live", "game_ended", "reported", "result_pending"]
        cursor = self._database[collection_name("matches")].find({"ladder_id": ladder_id, "status": {"$in": live}})
        async for doc in cursor:
            for side in doc.get("sides", []):
                if side.get("user_id") == user_id:
                    return dict(doc)
        return None

    async def find_ladder_matches(self, ladder_id: str, statuses: list[str]) -> list[dict[str, Any]]:
        """List the ladder's matches in any of the given statuses."""
        cursor = self._database[collection_name("matches")].find({"ladder_id": ladder_id, "status": {"$in": statuses}})
        return [doc async for doc in cursor]

    async def find_rating_history(self, ladder_id: str, user_id: str) -> list[dict[str, Any]]:
        """List a player's rating-history lines (ascending)."""
        cursor = (
            self._database[collection_name("rating_history")]
            .find({"ladder_id": ladder_id, "user_id": user_id})
            .sort("at", 1)
        )
        return [doc async for doc in cursor]


def collection_name(kind: str) -> str:
    """Map a seed collection kind to its MongoDB name."""
    names = {
        "maps": "maps",
        "civs": "factions",
        "rules": "rules",
        "map_pools": "map_pools",
        "map_packs": "map_packs",
        "map_pool_history": "map_pool_history",
        "seasons": "seasons",
        "ladders": "ladders",
        "players": "players",
        "matches": "matches",
        "rating_history": "rating_history",
    }
    return names[kind]


class NullAudit:
    """No-op audit seam for seeding (catalog writes are audited as a batch)."""

    async def record(self, action: str, payload: dict[str, Any]) -> None:
        """Accept the audit line and drop it."""
        return None


async def seed_aoe2(
    database: Any,
    data: dict[str, Any],
    now: int | None = None,
    ladder_seeder: Any = None,
) -> dict[str, Any]:
    """Idempotently seed the AoE2 catalog and pools.

    Returns per-section created counts. Re-running skips anything that
    already exists (same ids), so a crashed deployment can just re-seed.
    ``ladder_seeder`` (optional, injected by a mod) seeds the mod-owned
    ``ladders``/``seasons`` sections of the same YAML document.
    """
    now_ms = now if now is not None else int(time.time() * 1000)
    game_key = validate_game_key(str(data["game_key"]))
    adapter = MongoAoE2Database(database)
    game_data = GameDataService(adapter, audit=NullAudit())
    result: dict[str, Any] = {"game_key": game_key, "maps": 0, "civs": 0, "map_pools": 0, "ladders": 0, "seasons": 0}
    counts = await game_data.seed_from_data(game_key, data)
    result["maps"] = counts["maps"]
    result["factions"] = counts["factions"]
    result["rules"] = counts.get("rules", 0)
    for spec in data.get("map_pools", []) or []:
        pool_id = f"map_pool:{game_key}:{spec['name']}"
        if await game_data.get_map_pool(pool_id) is None:
            map_ids = tuple(f"map:{game_key}:{m}" for m in spec.get("maps", []))
            await game_data.create_map_pool(
                game_key,
                spec["name"],
                map_ids=map_ids,
                description=spec.get("description", ""),
            )
            result["map_pools"] += 1
    if ladder_seeder is not None:
        await ladder_seeder(adapter, data, game_key, now_ms, result)
    return result
