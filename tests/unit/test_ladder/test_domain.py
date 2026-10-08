"""Acceptance tests for the ladder domain core (kingdoms-services#134, reference §9)."""

from __future__ import annotations

from typing import Any

import pytest
from tests.unit.test_core.test_game_data import FakeGameDataDatabase

from kingdoms.mods.ladder.matchmaking import QueueEntry, compatible, matchmaking_pass
from kingdoms.mods.ladder.models import (
    LADDERS_COLLECTION,
    LIVE_MATCH_STATUSES,
    MATCH_STATUS_CANCELED,
    MATCH_STATUS_COMPLETED,
    MATCH_STATUS_CREATED,
    MATCH_STATUS_RESULT_PENDING,
    MATCH_STATUS_STARTED,
    MATCHES_COLLECTION,
    PLAYERS_COLLECTION,
    RATING_HISTORY_COLLECTION,
    LadderSettingsModel,
    MatchModel,
)
from kingdoms.mods.ladder.rating import EloRatingSystem, Glicko2RatingSystem
from kingdoms.mods.ladder.service import (
    ActiveMatchError,
    LadderError,
    LadderService,
    NotRegisteredError,
    NullGameGateway,
    QueueStateError,
)


class FakeLadderDatabase:
    """In-memory LadderDatabase over per-collection docs."""

    def __init__(self) -> None:
        self.collections: dict[str, dict[str, dict[str, Any]]] = {}

    def _collection(self, name: str) -> dict[str, dict[str, Any]]:
        return self.collections.setdefault(name, {})

    async def upsert_entry(self, collection: str, document: dict[str, Any]) -> None:
        self._collection(collection)[document["_id"]] = document

    async def find_entry(self, collection: str, entry_id: str) -> dict[str, Any] | None:
        return self._collection(collection).get(entry_id)

    async def find_ladder_by_owner(self, owner_ref: str, game_key: str) -> dict[str, Any] | None:
        for d in self._collection(LADDERS_COLLECTION).values():
            if d["owner_ref"] == owner_ref and d["game_key"] == game_key:
                return d
        return None

    async def find_player(self, ladder_id: str, user_id: str) -> dict[str, Any] | None:
        return self._collection(PLAYERS_COLLECTION).get(f"player:{ladder_id}:{user_id}")

    async def find_queued_players(self, ladder_id: str) -> list[dict[str, Any]]:
        return [d for d in self._collection(PLAYERS_COLLECTION).values() if d.get("queued_at") is not None]

    async def find_ladder_players(self, ladder_id: str) -> list[dict[str, Any]]:
        return [d for d in self._collection(PLAYERS_COLLECTION).values() if d.get("ladder_id") == ladder_id]

    async def find_active_match(self, ladder_id: str, user_id: str) -> dict[str, Any] | None:
        for d in self._collection(MATCHES_COLLECTION).values():
            m = MatchModel.from_mongo(d)
            if m.ladder_id == ladder_id and m.status in LIVE_MATCH_STATUSES and m.side_of(user_id):
                return d
        return None

    async def find_ladder_matches(self, ladder_id: str, statuses: list[str]) -> list[dict[str, Any]]:
        return [
            d
            for d in self._collection(MATCHES_COLLECTION).values()
            if d["ladder_id"] == ladder_id and d["status"] in statuses
        ]

    async def find_rating_history(self, ladder_id: str, user_id: str) -> list[dict[str, Any]]:
        rows = [d for d in self._collection(RATING_HISTORY_COLLECTION).values() if d["user_id"] == user_id]
        return sorted(rows, key=lambda d: d["created_at"])


class FakeEvents:
    """In-memory intent recorder."""

    def __init__(self) -> None:
        self.intents: list[tuple[str, dict[str, Any]]] = []

    async def emit(self, intent: str, payload: dict[str, Any]) -> None:
        self.intents.append((intent, payload))


GAME = "aoe2"
OWNER = "guild:1"
NOW = 1_000_000


def _game_data() -> Any:
    from kingdoms.core.services.game_data import GameDataService

    return GameDataService(FakeGameDataDatabase(), None)


async def _ladder_with_pool(svc: LadderService, game_data: Any) -> str:
    ladder = await svc.create_ladder(OWNER, "Ladder 1v1", GAME, now=NOW)
    m = await game_data.create_map(GAME, "Arabia", filename="arabia.v0")
    m2 = await game_data.create_map(GAME, "Arena", filename="arena.v0")
    pool = await game_data.create_map_pool(GAME, "P", map_ids=(m.id, m2.id))
    await svc.set_active_pool(ladder.id, pool.id)
    return ladder.id


def _env(gateway: Any = None) -> tuple[LadderService, Any, FakeEvents]:
    db, events, game_data = FakeLadderDatabase(), FakeEvents(), _game_data()
    svc = LadderService(db, game_data, gateway, events)
    return svc, game_data, events


async def _seed_pair(svc: LadderService, ladder_id: str) -> None:
    await svc.register_player(ladder_id, "u1", "Alice", now=NOW)
    await svc.register_player(ladder_id, "u2", "Bob", now=NOW)


# ── Matchmaking unit ─────────────────────────────────────────────────────


def test_reciprocal_window_widens_with_wait() -> None:
    s = LadderSettingsModel()
    fresh = QueueEntry("a", 1000, NOW)
    waiting = QueueEntry("b", 1160, NOW - 90_000)
    assert not compatible(fresh, waiting, s, NOW)
    both_waiting = QueueEntry("c", 1160, NOW - 90_000)
    assert compatible(waiting, both_waiting, s, NOW)


def test_window_caps_at_max() -> None:
    s = LadderSettingsModel()
    long_wait = QueueEntry("a", 1000, NOW - 10_000_000)
    far = QueueEntry("b", 1500, NOW - 10_000_000)
    assert not compatible(long_wait, far, s, NOW)


def test_two_players_always_match_when_compatible() -> None:
    s = LadderSettingsModel()
    e = [QueueEntry("a", 1000, NOW - 60_000), QueueEntry("b", 1005, NOW - 60_000)]
    assert len(matchmaking_pass(e, s, NOW)) == 1


def test_blossom_maximum_matching_odd_cycle() -> None:
    s = LadderSettingsModel()
    entries = [QueueEntry(str(i), 1000 + 30 * i, NOW - 300_000) for i in range(5)]
    pairs = matchmaking_pass(entries, s, NOW)
    matched = {p.host_user_id for p in pairs} | {p.guest_user_id for p in pairs}
    assert len(pairs) == 2
    assert len(matched) == 4


def test_blossom_prefers_more_matches() -> None:
    s = LadderSettingsModel(base_elo_threshold=100)
    # 0-1, 0-2, 1-3, 2-3 compatible; maximum matching = 2 pairs.
    entries = [QueueEntry(str(i), 1000, NOW - 300_000) for i in range(4)]
    pairs = matchmaking_pass(entries, s, NOW)
    assert len(pairs) == 2


# ── Rating systems ────────────────────────────────────────────────────────


def test_elo_reference_values() -> None:
    s = LadderSettingsModel()
    elo = EloRatingSystem()
    assert elo.initial_rating(s) == 1000.0
    assert elo.floor(s) == 800.0
    delta, k, _ = elo.apply(s, 1000, {}, 1000, {}, True, 0)
    assert k == 60.0
    assert delta == pytest.approx(30.0)
    delta2, k2, _ = elo.apply(s, 1000, {}, 1000, {}, True, 10)
    assert k2 == 32.0
    assert delta2 == pytest.approx(16.0)


def test_elo_caps_delta() -> None:
    s = LadderSettingsModel()
    elo = EloRatingSystem()
    delta, _, _ = elo.apply(s, 1000, {}, 2000, {}, True, 0)
    assert delta == pytest.approx(s.elo_max_gain)
    delta, _, _ = elo.apply(s, 2000, {}, 1000, {}, False, 0)
    assert delta == pytest.approx(-s.elo_max_loss)


def test_glicko2_provisional_state() -> None:
    g = Glicko2RatingSystem()
    s = LadderSettingsModel()
    assert g.initial_state(s) == {"rd": 350.0, "volatility": 0.06}
    assert g.initial_rating(s) == 1500.0


def test_glicko2_apply_moves_rating() -> None:
    g = Glicko2RatingSystem()
    s = LadderSettingsModel()
    state = g.initial_state(s)
    delta, k, new_state = g.apply(s, 1500.0, state, 1500.0, state, True, 0)
    assert delta > 0
    assert k > 0
    assert new_state["rd"] < 350.0


# ── Domain: degraded full path (§9.1) with NullGameGateway ───────────────


@pytest.mark.asyncio
async def test_degraded_full_path_manual_report() -> None:
    svc, game_data, events = _env(NullGameGateway())
    ladder_id = await _ladder_with_pool(svc, game_data)
    await _seed_pair(svc, ladder_id)
    await svc.join_queue(ladder_id, "u1", NOW, has_game_profile=True)
    await svc.join_queue(ladder_id, "u2", NOW, has_game_profile=True)
    matches = await svc.run_matchmaking_pass(ladder_id, NOW)
    assert len(matches) == 1
    match = matches[0]
    assert match.status == MATCH_STATUS_CREATED
    assert match.origin == "matchmaking"
    match = await svc.mark_ready(match.id, "u1", NOW + 1)
    match = await svc.mark_ready(match.id, "u2", NOW + 2)
    assert match.status == MATCH_STATUS_STARTED
    assert match.map_id is not None
    assert match.map_snapshot["filename"] in ("arabia.v0", "arena.v0")
    match = await svc.report_result(match.id, "u1", "u2", NOW + 3)
    match = await svc.report_result(match.id, "u2", "u2", NOW + 4)
    assert match.status == "REPORTED"
    match = await svc.confirm_result(match.id, "u1", NOW + 5)
    assert match.status == MATCH_STATUS_COMPLETED
    assert match.winner_user_id == "u2"
    intents = [i for i, _ in events.intents]
    assert "match.created" in intents and "match.result_confirmed" in intents


@pytest.mark.asyncio
async def test_rating_invariant_sum_of_deltas() -> None:
    svc, game_data, _ = _env(NullGameGateway())
    ladder_id = await _ladder_with_pool(svc, game_data)
    await _seed_pair(svc, ladder_id)
    await svc.join_queue(ladder_id, "u1", NOW, has_game_profile=True)
    await svc.join_queue(ladder_id, "u2", NOW, has_game_profile=True)
    (match,) = await svc.run_matchmaking_pass(ladder_id, NOW)
    await svc.mark_ready(match.id, "u1", NOW)
    match = await svc.mark_ready(match.id, "u2", NOW)
    await svc.report_result(match.id, "u1", "u1", NOW)
    match = await svc.report_result(match.id, "u2", "u1", NOW)
    match = await svc.confirm_result(match.id, "u1", NOW)
    winner = await svc.get_player(ladder_id, match.winner_user_id)
    loser = await svc.get_player(ladder_id, match.loser_user_id)
    assert winner is not None and loser is not None
    history_w = await svc._db.find_rating_history(ladder_id, winner.user_id)
    history_l = await svc._db.find_rating_history(ladder_id, loser.user_id)
    assert sum(h["delta"] for h in history_w) == pytest.approx(winner.rating - 1000)
    assert sum(h["delta"] for h in history_l) == pytest.approx(loser.rating - 1000)
    assert winner.wins == 1 and loser.losses == 1
    assert winner.streak == 1 and loser.streak == -1


@pytest.mark.asyncio
async def test_rating_applied_once_idempotent_confirm() -> None:
    svc, game_data, _ = _env(NullGameGateway())
    ladder_id = await _ladder_with_pool(svc, game_data)
    await _seed_pair(svc, ladder_id)
    await svc.join_queue(ladder_id, "u1", NOW, has_game_profile=True)
    await svc.join_queue(ladder_id, "u2", NOW, has_game_profile=True)
    (match,) = await svc.run_matchmaking_pass(ladder_id, NOW)
    await svc.mark_ready(match.id, "u1", NOW)
    await svc.mark_ready(match.id, "u2", NOW)
    await svc.report_result(match.id, "u1", "u1", NOW)
    await svc.report_result(match.id, "u2", "u1", NOW)
    m1 = await svc.confirm_result(match.id, "u1", NOW)
    m2 = await svc.confirm_result(match.id, "u2", NOW)
    assert m1.status == m2.status == MATCH_STATUS_COMPLETED
    p1 = await svc.get_player(ladder_id, "u1")
    assert p1 is not None and p1.wins == 1


@pytest.mark.asyncio
async def test_contradictory_reports_cancel_and_reset() -> None:
    svc, game_data, _ = _env(NullGameGateway())
    ladder_id = await _ladder_with_pool(svc, game_data)
    await _seed_pair(svc, ladder_id)
    await svc.join_queue(ladder_id, "u1", NOW, has_game_profile=True)
    await svc.join_queue(ladder_id, "u2", NOW, has_game_profile=True)
    (match,) = await svc.run_matchmaking_pass(ladder_id, NOW)
    await svc.mark_ready(match.id, "u1", NOW)
    match = await svc.mark_ready(match.id, "u2", NOW)
    match = await svc.report_result(match.id, "u1", "u1", NOW)
    match = await svc.report_result(match.id, "u2", "u2", NOW)
    assert match.winner_user_id is None
    assert match.status == MATCH_STATUS_RESULT_PENDING
    assert match.invalid_report_attempts == 1


@pytest.mark.asyncio
async def test_cancel_before_confirmation_never_touches_rating() -> None:
    svc, game_data, _ = _env(NullGameGateway())
    ladder_id = await _ladder_with_pool(svc, game_data)
    await _seed_pair(svc, ladder_id)
    await svc.join_queue(ladder_id, "u1", NOW, has_game_profile=True)
    await svc.join_queue(ladder_id, "u2", NOW, has_game_profile=True)
    (match,) = await svc.run_matchmaking_pass(ladder_id, NOW)
    canceled = await svc.cancel_match(match.id, "u1", "wrong opponent", NOW)
    assert canceled.status == MATCH_STATUS_CANCELED
    p1 = await svc.get_player(ladder_id, "u1")
    assert p1 is not None and p1.rating == 1000 and p1.matches_count == 0
    assert await svc._db.find_rating_history(ladder_id, "u1") == []


@pytest.mark.asyncio
async def test_ready_deadline_expires_created_match() -> None:
    svc, game_data, _ = _env(NullGameGateway())
    ladder_id = await _ladder_with_pool(svc, game_data)
    await _seed_pair(svc, ladder_id)
    await svc.join_queue(ladder_id, "u1", NOW, has_game_profile=True)
    await svc.join_queue(ladder_id, "u2", NOW, has_game_profile=True)
    (match,) = await svc.run_matchmaking_pass(ladder_id, NOW)
    late = NOW + 10 * 60 * 1000
    await svc.run_matchmaking_pass(ladder_id, late)
    m = await svc.get_match(match.id)
    assert m is not None and m.status == MATCH_STATUS_CANCELED
    assert m.cancel_reason == "ready_deadline"


@pytest.mark.asyncio
async def test_invite_creates_match_directly() -> None:
    svc, game_data, _ = _env(NullGameGateway())
    ladder_id = await _ladder_with_pool(svc, game_data)
    await _seed_pair(svc, ladder_id)
    match = await svc.create_invite_match(ladder_id, "u1", "u2", NOW)
    assert match.origin == "invite"
    with pytest.raises(ActiveMatchError):
        await svc.create_invite_match(ladder_id, "u1", "u2", NOW)


@pytest.mark.asyncio
async def test_join_queue_requires_profile_and_registration() -> None:
    svc, game_data, _ = _env(NullGameGateway())
    ladder_id = await _ladder_with_pool(svc, game_data)
    await svc.register_player(ladder_id, "u1", "Alice", now=NOW)
    with pytest.raises(QueueStateError):
        await svc.join_queue(ladder_id, "u1", NOW, has_game_profile=False)
    with pytest.raises(NotRegisteredError):
        await svc.join_queue(ladder_id, "ghost", NOW, has_game_profile=True)


@pytest.mark.asyncio
async def test_leave_queue_blocked_with_created_match() -> None:
    svc, game_data, _ = _env(NullGameGateway())
    ladder_id = await _ladder_with_pool(svc, game_data)
    await _seed_pair(svc, ladder_id)
    await svc.join_queue(ladder_id, "u1", NOW, has_game_profile=True)
    await svc.join_queue(ladder_id, "u2", NOW, has_game_profile=True)
    (match,) = await svc.run_matchmaking_pass(ladder_id, NOW)
    with pytest.raises(QueueStateError):
        await svc.leave_queue(ladder_id, "u1")
    del match


@pytest.mark.asyncio
async def test_map_pick_respects_bans_and_fav_weights() -> None:
    svc, game_data, _ = _env(NullGameGateway())
    ladder_id = await _ladder_with_pool(svc, game_data)
    m_arabia = await game_data.get_map(f"map:{GAME}:Arabia")
    m_arena = await game_data.get_map(f"map:{GAME}:Arena")
    assert m_arabia and m_arena
    await svc.register_player(ladder_id, "u1", "Alice", now=NOW)
    await svc.register_player(ladder_id, "u2", "Bob", now=NOW)
    await svc.set_preferences(ladder_id, "u1", fav_map_ids=(m_arabia.id,), ban_map_ids=())
    await svc.set_preferences(ladder_id, "u2", fav_map_ids=(), ban_map_ids=(m_arabia.id,))
    await svc.join_queue(ladder_id, "u1", NOW, has_game_profile=True)
    await svc.join_queue(ladder_id, "u2", NOW, has_game_profile=True)
    (match,) = await svc.run_matchmaking_pass(ladder_id, NOW)
    await svc.mark_ready(match.id, "u1", NOW)
    match = await svc.mark_ready(match.id, "u2", NOW)
    assert match.map_id == m_arena.id


@pytest.mark.asyncio
async def test_preferences_disjoint_and_capped() -> None:
    svc, game_data, _ = _env(NullGameGateway())
    ladder_id = await _ladder_with_pool(svc, game_data)
    await svc.register_player(ladder_id, "u1", "Alice", now=NOW)
    m_arabia = await game_data.get_map(f"map:{GAME}:Arabia")
    m_arena = await game_data.get_map(f"map:{GAME}:Arena")
    assert m_arabia and m_arena
    with pytest.raises(LadderError, match="disjoint"):
        await svc.set_preferences(ladder_id, "u1", (m_arabia.id,), (m_arabia.id,))
    # The active pool (2 maps) owns the caps: derived fav quota is 1 (#222).
    caps = await svc.preference_caps(ladder_id)
    assert caps == (1, 1)
    p = await svc.set_preferences(ladder_id, "u1", (m_arabia.id, m_arena.id, "x", "y", "z"), ())
    assert len(p.fav_map_ids) == 1
    # An explicit pool quota overrides the derivation.
    pool = await game_data.get_map_pool(f"map_pool:{GAME}:P")
    assert pool is not None
    updated = await game_data.update_map_pool(pool.id, fav_quota=3, ban_quota=0)
    assert updated.fav_quota == 3 and updated.ban_quota == 0
    caps = await svc.preference_caps(ladder_id)
    assert caps == (3, 0)
    p = await svc.set_preferences(ladder_id, "u1", (m_arabia.id, m_arena.id), ())
    assert len(p.fav_map_ids) == 2


@pytest.mark.asyncio
async def test_ladder_unique_per_owner_and_game() -> None:
    svc, _, _ = _env(NullGameGateway())
    await svc.create_ladder(OWNER, "L1", GAME, now=NOW)
    with pytest.raises(LadderError, match="already exists"):
        await svc.create_ladder(OWNER, "L2", GAME, now=NOW)


@pytest.mark.asyncio
async def test_no_platform_or_game_ids_in_collections() -> None:
    svc, game_data, _ = _env(NullGameGateway())
    ladder_id = await _ladder_with_pool(svc, game_data)
    await _seed_pair(svc, ladder_id)
    await svc.join_queue(ladder_id, "u1", NOW, has_game_profile=True)
    await svc.join_queue(ladder_id, "u2", NOW, has_game_profile=True)
    (match,) = await svc.run_matchmaking_pass(ladder_id, NOW)
    await svc.mark_ready(match.id, "u1", NOW)
    await svc.mark_ready(match.id, "u2", NOW)
    await svc.report_result(match.id, "u1", "u1", NOW)
    await svc.report_result(match.id, "u2", "u1", NOW)
    await svc.confirm_result(match.id, "u1", NOW)
    db = svc._db
    forbidden = ("discord_id", "message_id", "channel_id", "role_id", "profile_id")
    for collection in db.collections.values():
        for doc in collection.values():
            for key in forbidden:
                assert key not in doc, f"{key} leaked in {doc.get('_id')}"
