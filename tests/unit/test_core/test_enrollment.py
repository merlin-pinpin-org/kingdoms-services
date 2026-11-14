"""Unit tests for the generic enrollment parcours (botforge-services#267)."""

from __future__ import annotations

from typing import Any

import pytest

from botforge.core.services.enrollment import (
    STATE_ACCEPTED,
    STATE_PENDING,
    STATE_REFUSED,
    EnrollmentClosedError,
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
        profile_games: set[str] | None = None,
    ) -> None:
        self.elo, self.matches, self.profile = elo, matches, profile
        self.profile_games = profile_games

    async def player_elo(self, user_id: str, game_key: str, leaderboard: str = "") -> int | None:
        return self.elo

    async def matches_in_window(
        self, user_id: str, game_key: str, days: int, leaderboard: str = ""
    ) -> int | None:
        return self.matches

    async def has_profile(self, user_id: str, game_key: str) -> bool:
        if self.profile_games is not None:
            return game_key in self.profile_games
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
    from botforge.core.services.enrollment import NotPendingError

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


async def test_elo_range_with_leaderboard_evaluates() -> None:
    """min_elo/max_elo accept a leaderboard — the ELO 1000-1500 RM 1v1 case."""
    db = MemoryDB()
    service = EnrollmentService(
        "demo",
        db,
        stats=StaticStats(elo=1200),
        criteria={
            "min_elo": {"value": 1000, "leaderboard": "rm_1v1"},
            "max_elo": {"value": 1500, "leaderboard": "rm_1v1"},
            "min_matches": {"count": 10, "days": 30, "leaderboard": "rm_1v1"},
        },
    )
    application = await service.apply("g1", "u1", "Alice", now=100)
    assert application.state == "accepted"
    reasons = " ".join(r.reason for r in application.criteria_results)
    assert "1000" in reasons and "1500" in reasons
    # outside the range: refused
    service2 = EnrollmentService(
        "demo",
        MemoryDB(),
        stats=StaticStats(elo=1800),
        criteria={"min_elo": {"value": 1000}, "max_elo": {"value": 1500}},
    )
    refused = await service2.apply("g1", "u1", "Alice", now=100)
    assert refused.state == "refused"


async def test_requires_profiles_checks_every_declared_game() -> None:
    """requires_profiles: an account on each declared game of the mod."""
    db = MemoryDB()
    service = EnrollmentService(
        "demo",
        db,
        stats=StaticStats(profile_games={"aoe2"}),
        criteria={"requires_profiles": ["aoe2", "aoe4"]},
    )
    refused = await service.apply("g1", "u1", "Alice", now=100)
    assert refused.state == "refused"
    assert "aoe4" in " ".join(r.reason for r in refused.criteria_results)


async def test_window_opens_at_refuses_before_and_accepts_inside() -> None:
    """opens_at/closes_at gate the apply itself."""
    db = MemoryDB()
    service = EnrollmentService("demo", db, criteria={"opens_at": "2030-01-01T00:00:00+00:00"})
    with pytest.raises(EnrollmentClosedError, match="not open yet"):
        await service.apply("g1", "u1", "Alice", now=100)
    application = await service.apply("g1", "u1", "Alice", now=int(
        __import__("datetime").datetime.fromisoformat("2030-06-01T00:00:00+00:00").timestamp()
    ))
    assert application.state == "pending"
    service2 = EnrollmentService("demo", db, criteria={"closes_at": "2030-01-01T00:00:00+00:00"})
    with pytest.raises(EnrollmentClosedError, match="closed"):
        await service2.apply("g2", "u1", "Alice", now=2_000_000_000)


async def test_window_opens_before_the_season_start() -> None:
    """opens_before_start: the inscriptions open N before the season begins."""
    db = MemoryDB()
    service = EnrollmentService("demo", db, criteria={"opens_before_start": "7d"})
    season_start = 1_000_000
    with pytest.raises(EnrollmentClosedError, match="not open yet"):
        await service.apply("g1", "u1", "Alice", season="s1", now=season_start - 8 * 86400, season_start=season_start)
    application = await service.apply(
        "g1", "u1", "Alice", season="s1", now=season_start - 6 * 86400, season_start=season_start
    )
    assert application.state == "pending"


async def test_suspended_mod_refuses_apply() -> None:
    """A suspended mod refuses the players' applications (admins still decide)."""

    class SuspendedGate:
        async def require_usable(self, guild_id: str, mod: str) -> None:
            from botforge.core.services.mod_lifecycle import ModSuspendedError

            raise ModSuspendedError(mod, guild_id, "maintenance")

    db = MemoryDB()
    service = EnrollmentService("demo", db, criteria={}, lifecycle=SuspendedGate())
    with pytest.raises(Exception, match="suspended"):
        await service.apply("g1", "u1", "Alice", now=100)
    # the admins still decide pending applications
    await db.upsert_application(
        {
            "_id": "demo:g1:u9",
            "mod": "demo",
            "guild_id": "g1",
            "user_id": "u9",
            "display_name": "Bob",
            "state": "pending",
            "season": None,
            "applied_at": 1,
        }
    )
    decided = await service.accept("demo:g1:u9", now=200)
    assert decided.state == "accepted"
