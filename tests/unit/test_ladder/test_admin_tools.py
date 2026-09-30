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
