"""Unit tests for the generic enrollment parcours (kingdoms-services#267)."""

from __future__ import annotations

from typing import Any

import pytest

from kingdoms.core.services.enrollment import (
    STATE_ACCEPTED,
    STATE_PENDING,
    STATE_REFUSED,
    EnrollmentService,
    parse_criteria,
)


class MemoryDB:
    """In-memory EnrollmentDatabase implementation."""

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}

    async def upsert_application(self, document: dict[str, Any]) -> None:
        self.docs[document["_id"]] = dict(document)

    async def find_application(self, application_id: str) -> dict[str, Any] | None:
        return self.docs.get(application_id)

    async def find_active_application(self, mod: str, guild_id: str, user_id: str) -> dict[str, Any] | None:
        for doc in self.docs.values():
            if (
                doc["mod"] == mod
                and doc["guild_id"] == guild_id
                and doc["user_id"] == user_id
                and doc["state"] == STATE_PENDING
            ):
                return doc
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


class StaticStats:
    """Deterministic EnrollmentStats for tests."""

    def __init__(
        self,
        elo: int | None = 1500,
        matches: int | None = 10,
        profile: bool = True,
    ) -> None:
        self.elo, self.matches, self.profile = elo, matches, profile

    async def player_elo(self, user_id: str, game_key: str) -> int | None:
        return self.elo

    async def matches_in_window(self, user_id: str, game_key: str, days: int) -> int | None:
        return self.matches

    async def has_profile(self, user_id: str, game_key: str) -> bool:
        return self.profile


class RecordingMembership:
    """Membership seam recording register calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def register(self, guild_id: str, user_id: str, display_name: str) -> Any:
        self.calls.append((guild_id, user_id, display_name))
        return None


def test_parse_criteria_validates_unknown_keys() -> None:
    with pytest.raises(ValueError, match="unknown enrollment criteria"):
        parse_criteria({"enrollment": {"not_a_criterion": True}})


def test_parse_criteria_accepts_the_known_library() -> None:
    criteria = parse_criteria(
        {"enrollment": {"min_elo": 1200, "min_matches": {"count": 5, "days": 30}, "requires_profile": True}}
    )
    assert criteria == {"min_elo": 1200, "min_matches": {"count": 5, "days": 30}, "requires_profile": True}


async def test_apply_without_criteria_queues_for_admin() -> None:
    db = MemoryDB()
    service = EnrollmentService("demo", db, criteria={})
    application = await service.apply("g1", "u1", "Alice", now=100)
    assert application.state == STATE_PENDING
    assert application.application_id == "demo:g1:u1"


async def test_apply_is_idempotent_while_pending() -> None:
    db = MemoryDB()
    service = EnrollmentService("demo", db, criteria={})
    first = await service.apply("g1", "u1", "Alice", now=100)
    second = await service.apply("g1", "u1", "Alice", now=200)
    assert second.application_id == first.application_id
    assert second.applied_at == first.applied_at


async def test_all_criteria_pass_auto_accepts_and_registers() -> None:
    db = MemoryDB()
    membership = RecordingMembership()
    service = EnrollmentService(
        "demo",
        db,
        stats=StaticStats(elo=1500, matches=10, profile=True),
        membership=membership,
        game_key="aoe2",
        criteria={"min_elo": 1200, "requires_profile": True},
    )
    application = await service.apply("g1", "u1", "Alice", now=100)
    assert application.state == STATE_ACCEPTED
    assert membership.calls == [("g1", "u1", "Alice")]


async def test_manual_flag_queues_even_when_criteria_pass() -> None:
    db = MemoryDB()
    membership = RecordingMembership()
    service = EnrollmentService(
        "demo",
        db,
        stats=StaticStats(elo=1500),
        membership=membership,
        game_key="aoe2",
        criteria={"min_elo": 1200, "manual": True},
    )
    application = await service.apply("g1", "u1", "Alice", now=100)
    assert application.state == STATE_PENDING
    assert membership.calls == []
    accepted = await service.accept(application.application_id, now=200, reason="welcome")
    assert accepted.state == STATE_ACCEPTED
    assert membership.calls == [("g1", "u1", "Alice")]


async def test_failing_criterion_refuses_with_reasons() -> None:
    db = MemoryDB()
    service = EnrollmentService(
        "demo",
        db,
        stats=StaticStats(elo=900),
        game_key="aoe2",
        criteria={"min_elo": 1200},
    )
    application = await service.apply("g1", "u1", "Alice", now=100)
    assert application.state == STATE_REFUSED
    assert application.reasons and "rating" in application.reasons[0]


async def test_min_matches_window_evaluates() -> None:
    db = MemoryDB()
    service = EnrollmentService(
        "demo",
        db,
        stats=StaticStats(matches=2),
        game_key="aoe2",
        criteria={"min_matches": {"count": 5, "days": 30}},
    )
    application = await service.apply("g1", "u1", "Alice", now=100)
    assert application.state == STATE_REFUSED
    assert any("matches" in r for r in application.reasons)


async def test_unknown_stat_degrades_to_manual_review_refusal_reason() -> None:
    db = MemoryDB()
    service = EnrollmentService(
        "demo",
        db,
        stats=StaticStats(elo=None),
        game_key="aoe2",
        criteria={"min_elo": 1200},
    )
    application = await service.apply("g1", "u1", "Alice", now=100)
    assert application.state == STATE_REFUSED
    assert any("manual review" in r for r in application.reasons)


async def test_refuse_records_reason_and_blocks_reaccept() -> None:
    db = MemoryDB()
    service = EnrollmentService("demo", db, criteria={})
    application = await service.apply("g1", "u1", "Alice", now=100)
    refused = await service.refuse(application.application_id, now=150, reason="insufficient level")
    assert refused.state == STATE_REFUSED
    assert refused.reasons == ("insufficient level",)
    from kingdoms.core.services.enrollment import NotPendingError

    with pytest.raises(NotPendingError):
        await service.accept(application.application_id)


async def test_seasonal_application_ids_include_the_season() -> None:
    db = MemoryDB()
    service = EnrollmentService("demo", db, criteria={})
    application = await service.apply("g1", "u1", "Alice", season="s1", now=100)
    assert application.application_id == "demo:g1:u1:s1"


async def test_dashboard_groups_by_state() -> None:
    db = MemoryDB()
    service = EnrollmentService("demo", db, criteria={})
    await service.apply("g1", "u1", "Alice", now=100)
    await service.apply("g1", "u2", "Bob", now=101)
    application = await service.apply("g1", "u3", "Carol", now=102)
    await service.accept(application.application_id, now=103)
    dashboard = await service.dashboard("g1")
    assert len(dashboard["pending"]) == 2
    assert len(dashboard["accepted"]) == 1
    assert dashboard["refused"] == []
    assert dashboard["total"] == 3
