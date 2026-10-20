"""clans mod — acceptance tests on the core seams (MockDiscord, in-memory)."""

from __future__ import annotations

from typing import Any

import pytest
from kingdoms.mods.clans import service as clans_service
from kingdoms.mods.clans.service import (
    AlreadyInClanError,
    ClanNameTakenError,
    ClanService,
    NotClanAdminError,
)

from kingdoms.core.services.enrollment import (
    STATE_ACCEPTED,
    STATE_PENDING,
    EnrollmentService,
)


class MemoryClansDB:
    """In-memory ClansDatabase."""

    def __init__(self) -> None:
        self.clans: dict[str, dict[str, Any]] = {}
        self.memberships: dict[str, dict[str, Any]] = {}

    async def upsert_clan(self, document: dict[str, Any]) -> None:
        self.clans[document["_id"]] = dict(document)

    async def find_clan(self, clan_id: str) -> dict[str, Any] | None:
        return self.clans.get(clan_id)

    async def find_clan_by_name(self, guild_id: str, name: str) -> dict[str, Any] | None:
        for clan in self.clans.values():
            if clan["guild_id"] == guild_id and clan["name"].casefold() == name.strip().casefold():
                return dict(clan)
        return None

    async def list_clans(self, guild_id: str) -> list[dict[str, Any]]:
        return [dict(c) for c in self.clans.values() if c["guild_id"] == guild_id]

    async def find_membership(self, guild_id: str, user_id: str) -> dict[str, Any] | None:
        key = f"clan:{guild_id}:{user_id}"
        return dict(self.memberships[key]) if key in self.memberships else None

    async def upsert_membership(self, document: dict[str, Any]) -> None:
        self.memberships[document["_id"]] = dict(document)

    async def delete_membership(self, guild_id: str, user_id: str) -> bool:
        key = f"clan:{guild_id}:{user_id}"
        return self.memberships.pop(key, None) is not None

    async def delete_clan(self, guild_id: str, clan_id: str) -> bool:
        return self.clans.pop(clan_id, None) is not None

    async def list_members(self, guild_id: str, clan_id: str) -> list[dict[str, Any]]:
        return [
            dict(m)
            for m in self.memberships.values()
            if m["guild_id"] == guild_id and m["clan_id"] == clan_id
        ]


class MemoryEnrollmentDB:
    """In-memory EnrollmentDatabase."""

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}

    async def upsert_application(self, document: dict[str, Any]) -> None:
        self.docs[document["_id"]] = dict(document)

    async def find_application(self, application_id: str) -> dict[str, Any] | None:
        return self.docs.get(application_id)

    async def find_active_application(self, mod: str, guild_id: str, user_id: str) -> dict[str, Any] | None:
        for doc in self.docs.values():
            if doc["mod"] == mod and doc["guild_id"] == guild_id and doc["user_id"] == user_id:
                if doc["state"] == STATE_PENDING:
                    return dict(doc)
        return None

    async def list_applications(
        self, mod: str, guild_id: str, state: str | None = None
    ) -> list[dict[str, Any]]:
        return [
            dict(d)
            for d in self.docs.values()
            if d["mod"] == mod and d["guild_id"] == guild_id and (state is None or d["state"] == state)
        ]

    async def count_members(self, mod: str, guild_id: str) -> int:
        return len([d for d in self.docs.values() if d["state"] == STATE_ACCEPTED])


def _wired() -> tuple[ClanService, MemoryEnrollmentDB]:
    enrollment_db = MemoryEnrollmentDB()
    enrollment = EnrollmentService("clans", enrollment_db, criteria={"manual": True})
    return ClanService(MemoryClansDB(), enrollment), enrollment_db


async def test_anyone_creates_a_clan_and_becomes_leader() -> None:
    service, _ = _wired()
    clan = await service.create_clan("g1", "u1", "Alice", "Les Loups")
    assert clan["name"] == "Les Loups"
    assert clan["leader_id"] == "u1"
    membership = await service._db.find_membership("g1", "u1")
    assert membership["role"] == "leader"


async def test_clan_name_is_unique_per_guild() -> None:
    service, _ = _wired()
    await service.create_clan("g1", "u1", "Alice", "Les Loups")
    with pytest.raises(ClanNameTakenError):
        await service.create_clan("g1", "u2", "Bob", "les loups")


async def test_one_clan_per_player() -> None:
    service, _ = _wired()
    await service.create_clan("g1", "u1", "Alice", "Les Loups")
    with pytest.raises(AlreadyInClanError):
        await service.create_clan("g1", "u1", "Alice", "Les Aigles")


async def test_mutual_agreement_join_parcours() -> None:
    """Apply → pending → the clan's officer approves → member."""
    service, _enrollment_db = _wired()
    clan = await service.create_clan("g1", "u1", "Alice", "Les Loups")
    await service._db.upsert_membership(
        {
            "_id": "clan:g1:u2",
            "guild_id": "g1",
            "clan_id": clan["_id"],
            "user_id": "u2",
            "display_name": "Bob",
            "role": "officer",
        }
    )
    application = await service.apply_to_clan("g1", "u3", "Carol", clan["_id"])
    assert application.state == STATE_PENDING
    from kingdoms.core.services.enrollment import Application

    shaped = Application(
        mod="clans",
        guild_id="g1",
        user_id="u3",
        display_name="Carol",
        state=STATE_PENDING,
        season=clan["_id"],
    )
    decided = await service.approve_application("g1", shaped, "u2")
    assert decided.state == STATE_ACCEPTED
    membership = await service._db.find_membership("g1", "u3")
    assert membership["role"] == "member"
    assert membership["clan_id"] == clan["_id"]


async def test_only_clan_admins_approve() -> None:
    service, _ = _wired()
    clan = await service.create_clan("g1", "u1", "Alice", "Les Loups")
    application = await service.apply_to_clan("g1", "u3", "Carol", clan["_id"])
    with pytest.raises(NotClanAdminError):
        await service.approve_application("g1", application, "u9")


async def test_dashboard_lists_clans_with_member_counts() -> None:
    service, _ = _wired()
    await service.create_clan("g1", "u1", "Alice", "Les Loups")
    await service.create_clan("g1", "u2", "Bob", "Les Aigles")
    rows = await service.dashboard("g1")
    assert sorted((r["name"], r["member_count"]) for r in rows) == [
        ("Les Aigles", 1),
        ("Les Loups", 1),
    ]


async def test_members_drill_down() -> None:
    service, _ = _wired()
    clan = await service.create_clan("g1", "u1", "Alice", "Les Loups")
    members = await service.members("g1", clan["_id"])
    assert [m["user_id"] for m in members] == ["u1"]
    with pytest.raises(KeyError):
        await service.members("g1", "clan:g1:999")


async def test_leader_cannot_leave_a_populated_clan() -> None:
    service, _ = _wired()
    clan = await service.create_clan("g1", "u1", "Alice", "Les Loups")
    await service._db.upsert_membership(
        {
            "_id": "clan:g1:u2",
            "guild_id": "g1",
            "clan_id": clan["_id"],
            "user_id": "u2",
            "display_name": "Bob",
            "role": "member",
        }
    )
    with pytest.raises(clans_service.ClansModError, match="hand it over"):
        await service.leave_clan("g1", "u1")


async def test_sole_leader_leaving_deletes_the_clan() -> None:
    service, _ = _wired()
    await service.create_clan("g1", "u1", "Alice", "Les Loups")
    await service.leave_clan("g1", "u1")
    assert await service.dashboard("g1") == []
