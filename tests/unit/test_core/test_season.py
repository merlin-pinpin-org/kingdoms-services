"""Unit tests for the SeasonService lifecycle (kingdoms-services#132)."""

from __future__ import annotations

from typing import Any

import pytest

from kingdoms.core.models.season import SEASON_STATE_ACTIVE, SEASON_STATE_ENDED, SEASON_STATE_SCHEDULED
from kingdoms.core.services.game_data import GameDataService
from kingdoms.core.services.season import (
    SeasonActiveError,
    SeasonNotScheduledError,
    SeasonService,
)
from tests.unit.test_core.test_game_data import FakeAudit, FakeGameDataDatabase

GAME = "aoe2"
LADDER = "ladder:1"


class FakeSeasonDatabase:
    """In-memory SeasonDatabase."""

    def __init__(self) -> None:
        self.seasons: dict[str, dict[str, Any]] = {}

    async def upsert_season(self, document: dict[str, Any]) -> None:
        self.seasons[document["_id"]] = document

    async def find_season(self, season_id: str) -> dict[str, Any] | None:
        return self.seasons.get(season_id)

    async def find_ladder_seasons(self, ladder_id: str) -> list[dict[str, Any]]:
        rows = [d for d in self.seasons.values() if d["ladder_id"] == ladder_id]
        return sorted(rows, key=lambda d: d["start_at"])

    async def find_active_season(self, ladder_id: str) -> dict[str, Any] | None:
        for d in self.seasons.values():
            if d["ladder_id"] == ladder_id and d["state"] == SEASON_STATE_ACTIVE:
                return d
        return None


class FakeEvents:
    """In-memory notification-intent recorder."""

    def __init__(self) -> None:
        self.intents: list[tuple[str, dict[str, Any]]] = []

    async def emit(self, intent: str, payload: dict[str, Any]) -> None:
        self.intents.append((intent, payload))


def _env() -> tuple[SeasonService, GameDataService, FakeSeasonDatabase, FakeEvents, FakeAudit]:
    game_data = GameDataService(FakeGameDataDatabase(), FakeAudit())
    db, events, audit = FakeSeasonDatabase(), FakeEvents(), FakeAudit()
    return SeasonService(db, game_data, events, audit), game_data, db, events, audit


async def _map_and_pool(game_data: GameDataService) -> str:
    m = await game_data.create_map(GAME, "Arabia", filename="arabia.v0")
    pool = await game_data.create_map_pool(GAME, "S1 Pool", map_ids=(m.id,))
    return pool.id


@pytest.mark.asyncio
async def test_create_scheduled_season() -> None:
    svc, game_data, _, _, audit = _env()
    pool_id = await _map_and_pool(game_data)
    season = await svc.create_season(LADDER, "Season 1", pool_id, start_at=1000, end_at=2000)
    assert season.state == SEASON_STATE_SCHEDULED
    assert season.reset_ratings is False
    assert season.index == 1
    assert season.id == f"{LADDER}-1"
    assert ("season.create", {"season_id": season.id, "ladder_id": LADDER, "name": "Season 1"}) in audit.lines


@pytest.mark.asyncio
async def test_season_index_is_incremental_per_ladder() -> None:
    """Seasons are numbered 1, 2, 3 per ladder; ids embed the index."""
    svc, game_data, _, _, _ = _env()
    pool_id = await _map_and_pool(game_data)
    first = await svc.create_season(LADDER, "Winter", pool_id, start_at=1000)
    second = await svc.create_season(LADDER, "Spring", pool_id, start_at=2000)
    assert (first.index, first.id) == (1, f"{LADDER}-1")
    assert (second.index, second.id) == (2, f"{LADDER}-2")
    other = await svc.create_season("ladder:2", "Winter", pool_id, start_at=1000)
    assert (other.index, other.id) == (1, "ladder:2-1")


@pytest.mark.asyncio
async def test_create_season_validates_window() -> None:
    svc, game_data, _, _, _ = _env()
    pool_id = await _map_and_pool(game_data)
    with pytest.raises(ValueError, match="end must be after start"):
        await svc.create_season(LADDER, "Broken", pool_id, start_at=2000, end_at=1000)


@pytest.mark.asyncio
async def test_activate_season_switches_pool_transactionally() -> None:
    svc, game_data, _, events, _ = _env()
    pool_id = await _map_and_pool(game_data)
    season = await svc.create_season(LADDER, "S1", pool_id, start_at=1000)
    active = await svc.activate_season(season.id, now=1500)
    assert active.state == SEASON_STATE_ACTIVE
    assert active.activated_at == 1500
    assert ("pool.switched", {"ladder_id": LADDER, "map_pool_id": pool_id}) in events.intents
    assert await svc.get_active_season(LADDER) is not None


@pytest.mark.asyncio
async def test_activate_twice_rejected() -> None:
    svc, game_data, _, _, _ = _env()
    pool_id = await _map_and_pool(game_data)
    season = await svc.create_season(LADDER, "S1", pool_id, start_at=1000)
    await svc.activate_season(season.id, now=1500)
    with pytest.raises(SeasonNotScheduledError):
        await svc.activate_season(season.id, now=1600)


@pytest.mark.asyncio
async def test_second_season_blocked_while_one_active() -> None:
    svc, game_data, _, _, _ = _env()
    pool_id = await _map_and_pool(game_data)
    s1 = await svc.create_season(LADDER, "S1", pool_id, start_at=1000)
    s2 = await svc.create_season(LADDER, "S2", pool_id, start_at=2000)
    await svc.activate_season(s1.id, now=1500)
    with pytest.raises(SeasonActiveError):
        await svc.activate_season(s2.id, now=2500)


@pytest.mark.asyncio
async def test_end_season() -> None:
    svc, game_data, _, _, _ = _env()
    pool_id = await _map_and_pool(game_data)
    season = await svc.create_season(LADDER, "S1", pool_id, start_at=1000)
    await svc.activate_season(season.id, now=1500)
    ended = await svc.end_season(season.id, now=9000)
    assert ended.state == SEASON_STATE_ENDED
    assert ended.ended_at == 9000
    assert await svc.get_active_season(LADDER) is None
    with pytest.raises(SeasonActiveError):
        await svc.end_season(season.id, now=9500)


@pytest.mark.asyncio
async def test_activate_after_end_allowed() -> None:
    svc, game_data, _, _, _ = _env()
    pool_id = await _map_and_pool(game_data)
    s1 = await svc.create_season(LADDER, "S1", pool_id, start_at=1000)
    s2 = await svc.create_season(LADDER, "S2", pool_id, start_at=3000)
    await svc.activate_season(s1.id, now=1500)
    await svc.end_season(s1.id, now=2000)
    active2 = await svc.activate_season(s2.id, now=3500)
    assert active2.state == SEASON_STATE_ACTIVE


@pytest.mark.asyncio
async def test_reset_ratings_season_emits_ratings_reset() -> None:
    svc, game_data, _, events, _ = _env()
    pool_id = await _map_and_pool(game_data)
    season = await svc.create_season(LADDER, "Reset S", pool_id, start_at=1000, reset_ratings=True)
    await svc.activate_season(season.id, now=1500)
    intents = [i for i, _ in events.intents]
    assert "pool.switched" in intents
    assert "ratings.reset" in intents


@pytest.mark.asyncio
async def test_no_reset_no_ratings_intent() -> None:
    svc, game_data, _, events, _ = _env()
    pool_id = await _map_and_pool(game_data)
    season = await svc.create_season(LADDER, "Plain S", pool_id, start_at=1000, reset_ratings=False)
    await svc.activate_season(season.id, now=1500)
    assert all(i != "ratings.reset" for i, _ in events.intents)


@pytest.mark.asyncio
async def test_list_seasons_ascending() -> None:
    svc, game_data, _, _, _ = _env()
    pool_id = await _map_and_pool(game_data)
    await svc.create_season(LADDER, "B", pool_id, start_at=2000)
    await svc.create_season(LADDER, "A", pool_id, start_at=1000)
    assert [s.name for s in await svc.list_seasons(LADDER)] == ["A", "B"]


@pytest.mark.asyncio
async def test_unknown_season_rejected() -> None:
    svc, _, _, _, _ = _env()
    with pytest.raises(ValueError, match="unknown season"):
        await svc.activate_season("season:x:none", now=1)
