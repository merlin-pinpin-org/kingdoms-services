"""Per-mod seasons wiring: one factory, every seasonal mod (kingdoms-services#267).

The pattern the ladder mod cables by hand (SeasonService + GameDataService
+ SeasonRolesService around the mod's season scope) becomes one core seam:
a mod declares ``seasonal: true`` and calls ``build_mod_season_stack`` —
it gets the season machinery (one live season max, transactional map-pool
rotations, per-season roles), the enrollment bound to the active season,
and the admin dashboard data, without owning any of the plumbing.

Season scope: ``<mod>:<game key>:<guild id>`` (``mod_scope``) — the visible
ids stay the core ones (``<scope>:<index>``, one active max enforced by
``SeasonService``).

Reference: kingdoms-services#267 (spec §2), the ladder wiring it replaces
(``kingdoms.mods.ladder.commands``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from kingdoms.core.ids import mod_scope
from kingdoms.core.services.enrollment import EnrollmentService
from kingdoms.core.services.season import SeasonService
from kingdoms.core.services.season_roles import SeasonRolesService


@dataclass(frozen=True, slots=True)
class ModSeasonStack:
    """The wired season machinery for one mod+guild."""

    mod: str
    guild_id: str
    scope: str
    seasons: SeasonService
    season_roles: SeasonRolesService | None
    enrollment: EnrollmentService | None

    async def active_season(self) -> Any | None:
        """Return the guild's active season model; None when none is live."""
        return await self.seasons.get_active_season(self.scope)


def build_mod_season_stack(
    *,
    mod: str,
    guild_id: str,
    game_key: str,
    database: Any,
    game_data: Any,
    season_roles: SeasonRolesService | None = None,
    enrollment_stats: Any = None,
    enrollment_membership: Any = None,
    enrollment_criteria: dict[str, Any] | None = None,
) -> ModSeasonStack:
    """Wire the generic season stack for one mod on one guild.

    ``database`` is the SeasonDatabase adapter (e.g. MongoAoE2Database);
    ``game_data`` the GameDataService (pool switches); ``season_roles``
    the provisioned SeasonRolesService (per-season player/staff roles);
    the enrollment seams are the ones of ``EnrollmentService``.
    """
    scope = mod_scope(mod, game_key, guild_id)
    seasons = SeasonService(database, game_data)
    enrollment = (
        EnrollmentService(
            mod,
            _ModEnrollmentDatabase(database, scope),
            stats=enrollment_stats,
            membership=enrollment_membership,
            game_key=game_key,
            criteria=enrollment_criteria,
        )
        if enrollment_criteria is not None
        else None
    )
    return ModSeasonStack(
        mod=mod,
        guild_id=guild_id,
        scope=scope,
        seasons=seasons,
        season_roles=season_roles,
        enrollment=enrollment,
    )


class _ModEnrollmentDatabase:
    """Store applications in the ``<scope>:applications`` Mongo collection.

    One document store serves both seams: the season database already owns
    the Mongo connection, the applications live in their own collection.
    """

    def __init__(self, database: Any, scope: str) -> None:
        self._database = database
        self._scope = scope

    async def _collection(self) -> Any:
        inner = getattr(self._database, "_database", None)
        if inner is None:
            raise ValueError("the season database holds no inner Mongo database")
        return inner[f"{self._scope}:applications"]

    async def upsert_application(self, document: dict[str, Any]) -> None:
        """Insert or replace one application document."""
        collection = await self._collection()
        await collection.replace_one({"_id": document["_id"]}, document, upsert=True)

    async def find_application(self, application_id: str) -> dict[str, Any] | None:
        """Return one application document; None when unknown."""
        collection = await self._collection()
        doc = await collection.find_one({"_id": application_id})
        return dict(doc) if doc is not None else None

    async def find_active_application(self, mod: str, guild_id: str, user_id: str) -> dict[str, Any] | None:
        """Return the player's pending application for this mod+guild."""
        collection = await self._collection()
        doc = await collection.find_one({"mod": mod, "guild_id": guild_id, "user_id": user_id, "state": "pending"})
        return dict(doc) if doc is not None else None

    async def list_applications(self, mod: str, guild_id: str, state: str | None = None) -> list[dict[str, Any]]:
        """List the guild's applications, optionally filtered by state."""
        collection = await self._collection()
        query: dict[str, Any] = {"mod": mod, "guild_id": guild_id}
        if state is not None:
            query["state"] = state
        cursor = collection.find(query)
        docs: list[dict[str, Any]] = [dict(doc) async for doc in cursor]
        return docs

    async def count_members(self, mod: str, guild_id: str) -> int:
        """Count accepted applications (the enrolled members)."""
        collection = await self._collection()
        count = await collection.count_documents({"mod": mod, "guild_id": guild_id, "state": "accepted"})
        return int(count)
