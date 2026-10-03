"""Unit tests for the ladder platform surface (kingdoms-services#135)."""

from __future__ import annotations

from typing import Any

import pytest
from tests.unit.test_ladder.test_domain import (
    GAME,
    NOW,
    OWNER,
    FakeEvents,
    FakeLadderDatabase,
    _game_data,
    _seed_pair,
)

from kingdoms.mods.ladder.models import (
    MATCH_STATUS_COMPLETED,
    MATCH_STATUS_CREATED,
)
from kingdoms.mods.ladder.service import LadderService, NullGameGateway
from kingdoms.mods.ladder.surface import (
    ACTION_CANCEL_MATCH,
    ACTION_CONFIRM_RESULT,
    ACTION_INVITE_PLAYER,
    ACTION_JOIN_QUEUE,
    ACTION_LEAVE_QUEUE,
    ACTION_READY,
    ACTION_REPORT_RESULT,
    LadderSurface,
)


def _env() -> tuple[LadderSurface, LadderService]:
    db, events, game_data = FakeLadderDatabase(), FakeEvents(), _game_data()
    svc = LadderService(db, game_data, NullGameGateway(), events)
    return LadderSurface(svc), svc


async def _ladder(svc: LadderService, game_data: Any) -> str:
    ladder = await svc.create_ladder(OWNER, "L1", GAME, now=NOW)
    m = await game_data.create_map(GAME, "Arabia", filename="arabia.v0")
    m2 = await game_data.create_map(GAME, "Arena", filename="arena.v0")
    pool = await game_data.create_map_pool(GAME, "P", map_ids=(m.id, m2.id))
    await svc.set_active_pool(ladder.id, pool.id)
    return ladder.id


@pytest.mark.asyncio
async def test_actions_flow_join_to_completion() -> None:
    surface, svc = _env()
    ladder_id = await _ladder(svc, svc._game_data)
    await _seed_pair(svc, ladder_id)
    r = await surface.execute(ACTION_JOIN_QUEUE, ladder_id, "u1", NOW, has_game_profile=True)
    assert r.ok, r.reason
    r = await surface.execute(ACTION_JOIN_QUEUE, ladder_id, "u2", NOW, has_game_profile=True)
    assert r.ok
    (match,) = await svc.run_matchmaking_pass(ladder_id, NOW)
    r = await surface.execute(ACTION_READY, ladder_id, "u1", NOW + 1, match_id=match.id)
    assert r.ok and r.data["status"] == MATCH_STATUS_CREATED
    r = await surface.execute(ACTION_READY, ladder_id, "u2", NOW + 2, match_id=match.id)
    assert r.ok and r.data["status"] == "STARTED"
    r = await surface.execute(ACTION_REPORT_RESULT, ladder_id, "u1", NOW + 3, match_id=match.id, winner_user_id="u1")
    assert r.ok
    r = await surface.execute(ACTION_REPORT_RESULT, ladder_id, "u2", NOW + 4, match_id=match.id, winner_user_id="u1")
    assert r.ok
    r = await surface.execute(ACTION_CONFIRM_RESULT, ladder_id, "u1", NOW + 5, match_id=match.id)
    assert r.ok and r.data["status"] == MATCH_STATUS_COMPLETED


@pytest.mark.asyncio
async def test_join_queue_refused_without_profile() -> None:
    surface, svc = _env()
    ladder_id = await _ladder(svc, svc._game_data)
    await _seed_pair(svc, ladder_id)
    r = await surface.execute(ACTION_JOIN_QUEUE, ladder_id, "u1", NOW, has_game_profile=False)
    assert not r.ok and r.reason


@pytest.mark.asyncio
async def test_cancel_requires_reason() -> None:
    surface, svc = _env()
    ladder_id = await _ladder(svc, svc._game_data)
    await _seed_pair(svc, ladder_id)
    await svc.join_queue(ladder_id, "u1", NOW, has_game_profile=True)
    await svc.join_queue(ladder_id, "u2", NOW, has_game_profile=True)
    (match,) = await svc.run_matchmaking_pass(ladder_id, NOW)
    r = await surface.execute(ACTION_CANCEL_MATCH, ladder_id, "u1", NOW, match_id=match.id, reason="")
    assert not r.ok and r.reason == "reason_mandatory"
    r = await surface.execute(ACTION_CANCEL_MATCH, ladder_id, "u1", NOW, match_id=match.id, reason="bye")
    assert r.ok


@pytest.mark.asyncio
async def test_invite_action() -> None:
    surface, svc = _env()
    ladder_id = await _ladder(svc, svc._game_data)
    await _seed_pair(svc, ladder_id)
    r = await surface.execute(ACTION_INVITE_PLAYER, ladder_id, "u1", NOW, guest_user_id="u2")
    assert r.ok and r.data and "match_id" in r.data
    r = await surface.execute(ACTION_INVITE_PLAYER, ladder_id, "u1", NOW)
    assert not r.ok and r.reason == "missing_guest"


@pytest.mark.asyncio
async def test_unknown_action_refused() -> None:
    surface, svc = _env()
    ladder_id = await _ladder(svc, svc._game_data)
    r = await surface.execute("hack_the_planet", ladder_id, "u1", NOW)
    assert not r.ok and r.reason == "unknown_action"


@pytest.mark.asyncio
async def test_leave_queue_blocked_with_created_match() -> None:
    surface, svc = _env()
    ladder_id = await _ladder(svc, svc._game_data)
    await _seed_pair(svc, ladder_id)
    await svc.join_queue(ladder_id, "u1", NOW, has_game_profile=True)
    await svc.join_queue(ladder_id, "u2", NOW, has_game_profile=True)
    (match,) = await svc.run_matchmaking_pass(ladder_id, NOW)
    r = await surface.execute(ACTION_LEAVE_QUEUE, ladder_id, "u1", NOW)
    assert not r.ok
    del match


@pytest.mark.asyncio
async def test_queue_view_rows() -> None:
    surface, svc = _env()
    ladder_id = await _ladder(svc, svc._game_data)
    await _seed_pair(svc, ladder_id)
    await svc.join_queue(ladder_id, "u1", NOW, has_game_profile=True)
    await svc.join_queue(ladder_id, "u2", NOW, has_game_profile=True)
    rows = await surface.queue_view(ladder_id, NOW + 60_000)
    assert len(rows) == 2
    assert rows[0].wait_seconds == 60
    assert rows[0].threshold > 60


@pytest.mark.asyncio
async def test_leaderboard_view_rows() -> None:
    surface, svc = _env()
    ladder_id = await _ladder(svc, svc._game_data)
    await _seed_pair(svc, ladder_id)
    rows = await surface.leaderboard_view(ladder_id)
    assert len(rows) == 2
    assert rows[0].rank == 1 and rows[0].rating == 1000


@pytest.mark.asyncio
async def test_match_timeline_and_actions() -> None:
    surface, svc = _env()
    ladder_id = await _ladder(svc, svc._game_data)
    await _seed_pair(svc, ladder_id)
    await svc.join_queue(ladder_id, "u1", NOW, has_game_profile=True)
    await svc.join_queue(ladder_id, "u2", NOW, has_game_profile=True)
    (match,) = await svc.run_matchmaking_pass(ladder_id, NOW)
    await svc.mark_ready(match.id, "u1", NOW)
    match = await svc.mark_ready(match.id, "u2", NOW)
    timeline = surface.match_timeline(match)
    labels = [e.label for e in timeline]
    assert labels == ["created", "ready", "started"]
    actions = surface.available_actions(match, "u1")
    assert ACTION_READY in actions and ACTION_CANCEL_MATCH in actions
    assert surface.available_actions(match, "outsider") == []
