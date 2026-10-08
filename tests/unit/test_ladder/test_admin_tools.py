"""Unit tests for the ladder rating admin tools (kingdoms-services#136)."""

from __future__ import annotations

import pytest
from tests.unit.test_ladder.test_domain import (
    GAME,
    NOW,
    OWNER,
    FakeEvents,
    FakeLadderDatabase,
    _game_data,
    _ladder_with_pool,
    _seed_pair,
)

from kingdoms.mods.ladder.admin_tools import LadderRatingTools, RatingToolError
from kingdoms.mods.ladder.models import MATCH_STATUS_CANCELED
from kingdoms.mods.ladder.service import LadderService, NullGameGateway


def _env() -> tuple[LadderRatingTools, LadderService, list[tuple[str, dict[str, object]]]]:
    db, events, game_data = FakeLadderDatabase(), FakeEvents(), _game_data()
    svc = LadderService(db, game_data, NullGameGateway(), events)
    lines: list[tuple[str, dict[str, object]]] = []

    class Audit:
        async def record(self, action: str, payload: dict[str, object]) -> None:
            lines.append((action, payload))

    return LadderRatingTools(svc, Audit()), svc, lines


async def _completed_match(svc: LadderService, ladder_id: str) -> object:
    await _seed_pair(svc, ladder_id)
    await svc.join_queue(ladder_id, "u1", NOW, has_game_profile=True)
    await svc.join_queue(ladder_id, "u2", NOW, has_game_profile=True)
    (match,) = await svc.run_matchmaking_pass(ladder_id, NOW)
    await svc.mark_ready(match.id, "u1", NOW)
    await svc.mark_ready(match.id, "u2", NOW)
    await svc.report_result(match.id, "u1", "u1", NOW)
    await svc.report_result(match.id, "u2", "u1", NOW)
    return await svc.confirm_result(match.id, "u1", NOW)


@pytest.mark.asyncio
async def test_adjust_rating_with_reason() -> None:
    tools, svc, audit = _env()
    ladder = await svc.create_ladder(OWNER, "L1", GAME, now=NOW)
    await _seed_pair(svc, ladder.id)
    p = await tools.adjust_rating(ladder.id, "admin:1", "u1", 150, "bonus", NOW)
    assert p.rating == 1150
    assert any(a == "rating.adjust" for a, _ in audit)
    hist = await svc._db.find_rating_history(ladder.id, "u1")
    assert hist and hist[-1]["reason"] == "MANUAL_ADJUSTMENT"


@pytest.mark.asyncio
async def test_adjust_requires_reason_and_amount() -> None:
    tools, svc, _ = _env()
    ladder = await svc.create_ladder(OWNER, "L1", GAME, now=NOW)
    await _seed_pair(svc, ladder.id)
    with pytest.raises(RatingToolError, match="reason is mandatory"):
        await tools.adjust_rating(ladder.id, "admin:1", "u1", 50, " ", NOW)
    with pytest.raises(RatingToolError, match="non-zero"):
        await tools.adjust_rating(ladder.id, "admin:1", "u1", 0, "x", NOW)


@pytest.mark.asyncio
async def test_reset_ratings() -> None:
    tools, svc, audit = _env()
    ladder_id = await _ladder_with_pool(svc, svc._game_data)
    await _completed_match(svc, ladder_id)
    players = await tools.reset_ratings(ladder_id, "admin:1", "fresh start", NOW)
    assert all(p.rating == 1000 for p in players)
    assert any(a == "rating.reset" for a, _ in audit)


@pytest.mark.asyncio
async def test_cancel_completed_match_compensates() -> None:
    tools, svc, audit = _env()
    ladder_id = await _ladder_with_pool(svc, svc._game_data)
    match = await _completed_match(svc, ladder_id)
    canceled = await tools.cancel_match_with_compensation(match.id, "admin:1", "wrong result", NOW + 100)
    assert canceled.status == MATCH_STATUS_CANCELED
    p1 = await svc.get_player(match.ladder_id, "u1")
    p2 = await svc.get_player(match.ladder_id, "u2")
    assert p1.rating == 1000 and p2.rating == 1000
    assert p1.matches_count == 0 and p2.matches_count == 0
    hist1 = await svc._db.find_rating_history(match.ladder_id, "u1")
    assert sum(h["delta"] for h in hist1) == pytest.approx(0)
    assert any(a == "rating.cancel_match" for a, _ in audit)


@pytest.mark.asyncio
async def test_cancel_requires_completed_match() -> None:
    tools, svc, _ = _env()
    ladder = await svc.create_ladder(OWNER, "L1", GAME, now=NOW)
    await _seed_pair(svc, ladder.id)
    await svc.join_queue(ladder.id, "u1", NOW, has_game_profile=True)
    await svc.join_queue(ladder.id, "u2", NOW, has_game_profile=True)
    (match,) = await svc.run_matchmaking_pass(ladder.id, NOW)
    with pytest.raises(RatingToolError, match="not completed"):
        await tools.cancel_match_with_compensation(match.id, "admin:1", "x", NOW)


# ── Rating-system switch replay (kingdoms-services#142) ───────────────────


from kingdoms.mods.ladder.admin_tools import (  # noqa: E402
    LadderRatingSwitchService,
    RatingSystemSwitchError,
)


def _switch_env() -> tuple[LadderRatingSwitchService, LadderService]:
    db, events, game_data = FakeLadderDatabase(), FakeEvents(), _game_data()
    svc = LadderService(db, game_data, NullGameGateway(), events)

    class Audit:
        async def record(self, action: str, payload: dict[str, object]) -> None:
            del action, payload

    return LadderRatingSwitchService(svc, Audit()), svc


async def _two_completed_matches(svc: LadderService, ladder_id: str) -> None:
    await _seed_pair(svc, ladder_id)
    for i in range(2):
        t = NOW + i * 100_000
        await svc.join_queue(ladder_id, "u1", t, has_game_profile=True)
        await svc.join_queue(ladder_id, "u2", t, has_game_profile=True)
        (match,) = await svc.run_matchmaking_pass(ladder_id, t)
        await svc.mark_ready(match.id, "u1", t)
        match = await svc.mark_ready(match.id, "u2", t)
        await svc.report_result(match.id, "u1", "u1", t + 1)
        await svc.report_result(match.id, "u2", "u1", t + 2)
        await svc.confirm_result(match.id, "u1", t + 3)


@pytest.mark.asyncio
async def test_switch_replays_deterministically() -> None:
    switch, svc = _switch_env()
    ladder_id = await _ladder_with_pool(svc, svc._game_data)
    await _two_completed_matches(svc, ladder_id)
    summary = await switch.switch_rating_system(ladder_id, "admin:1", "glicko2", NOW)
    assert summary["changed"] and summary["replayed"] == 2
    p1 = await svc.get_player(ladder_id, "u1")
    assert p1 is not None and p1.rating_state.get("rd") is not None
    hist = await svc._db.find_rating_history(ladder_id, "u1")
    assert any(h["reason"] == "RECALCULATION" for h in hist)
    assert sum(h["delta"] for h in hist) == pytest.approx(p1.rating - 1000, abs=2.0)


@pytest.mark.asyncio
async def test_switch_refused_while_match_in_flight() -> None:
    switch, svc = _switch_env()
    ladder_id = await _ladder_with_pool(svc, svc._game_data)
    await _seed_pair(svc, ladder_id)
    await svc.join_queue(ladder_id, "u1", NOW, has_game_profile=True)
    await svc.join_queue(ladder_id, "u2", NOW, has_game_profile=True)
    (match,) = await svc.run_matchmaking_pass(ladder_id, NOW)
    with pytest.raises(RatingSystemSwitchError, match="in flight"):
        await switch.switch_rating_system(ladder_id, "admin:1", "glicko2", NOW)
    del match


@pytest.mark.asyncio
async def test_switch_refused_on_incomplete_history() -> None:
    switch, svc = _switch_env()
    ladder_id = await _ladder_with_pool(svc, svc._game_data)
    await _two_completed_matches(svc, ladder_id)
    from kingdoms.mods.ladder.models import MATCH_STATUS_COMPLETED

    docs = await svc._db.find_ladder_matches(ladder_id, [MATCH_STATUS_COMPLETED])
    docs[0]["rating_applied"] = None
    await svc._db.upsert_entry("matches", docs[0])
    with pytest.raises(RatingSystemSwitchError, match="incomplete"):
        await switch.switch_rating_system(ladder_id, "admin:1", "glicko2", NOW)


@pytest.mark.asyncio
async def test_switch_back_replays_equally() -> None:
    switch, svc = _switch_env()
    ladder_id = await _ladder_with_pool(svc, svc._game_data)
    await _two_completed_matches(svc, ladder_id)
    await switch.switch_rating_system(ladder_id, "admin:1", "glicko2", NOW)
    back = await switch.switch_rating_system(ladder_id, "admin:1", "elo", NOW)
    assert back["replayed"] == 2
    p1 = await svc.get_player(ladder_id, "u1")
    p2 = await svc.get_player(ladder_id, "u2")
    assert p1 is not None and p2 is not None
    from kingdoms.mods.ladder.models import LadderSettingsModel
    from kingdoms.mods.ladder.rating import EloRatingSystem

    elo, st = EloRatingSystem(), LadderSettingsModel()
    r1, r2 = elo.initial_rating(st), elo.initial_rating(st)
    d1, _, _ = elo.apply(st, r1, {}, r2, {}, True, 0)
    d2, _, _ = elo.apply(st, r2, {}, r1, {}, False, 0)
    r1b, r2b = r1 + d1, r2 + d2
    d1b, _, _ = elo.apply(st, r1b, {}, r2b, {}, True, 1)
    d2b, _, _ = elo.apply(st, r2b, {}, r1b, {}, False, 1)
    assert p1.rating == int(r1b + d1b)
    assert p2.rating == int(r2b + d2b)


@pytest.mark.asyncio
async def test_switch_unknown_system_refused() -> None:
    switch, svc = _switch_env()
    ladder_id = await _ladder_with_pool(svc, svc._game_data)
    with pytest.raises(RatingSystemSwitchError, match="unknown rating system"):
        await switch.switch_rating_system(ladder_id, "admin:1", "chessdotgov", NOW)
