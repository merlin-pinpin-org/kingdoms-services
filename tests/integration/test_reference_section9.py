"""Reference §9 acceptance journeys (kingdoms-services#137).

End-to-end pass of the JeanJack V2.0 reference §9 acceptance criteria
against the real ladder domain stack with a mocked game gateway (§9.1):
full journey registration → queue → match → manual report → confirmation →
rating, platform independence invariants (§9.2), timing/matching properties
(§9.3/§9.4), rating invariants (§9.5), idempotent transitions (§9.7) and
non-admin guarantees (§9.8).

Each test names the §9 criterion it encodes; the rollout rehearsal against
the real Discord stack (§9.3-§9.8 live) is tracked in the issue itself.
"""

from __future__ import annotations

from typing import Any

import pytest

from kingdoms.mods.ladder.matchmaking import QueueEntry, matchmaking_pass
from kingdoms.mods.ladder.models import (
    MATCH_STATUS_CREATED,
    MATCHES_COLLECTION,
    LadderSettingsModel,
)
from kingdoms.mods.ladder.service import (
    LadderError,
    LadderService,
    NotRegisteredError,
    NullGameGateway,
)
from tests.unit.test_ladder.test_domain import (
    NOW,
    FakeEvents,
    FakeLadderDatabase,
    _game_data,
    _ladder_with_pool,
)


def db_collections(svc: Any) -> dict[str, dict[str, Any]]:
    """Reach the fake DB's raw collections (platform-ID audit needs them)."""
    db: Any = svc._db
    collections: dict[str, dict[str, Any]] = db.collections
    return collections


async def _full_match(svc: LadderService, ladder_id: str, u1: str = "u1", u2: str = "u2") -> Any:
    """Drive one full matchmaking cycle to a COMPLETED match."""
    await svc.join_queue(ladder_id, u1, NOW, has_game_profile=True)
    await svc.join_queue(ladder_id, u2, NOW, has_game_profile=True)
    (match,) = await svc.run_matchmaking_pass(ladder_id, NOW)
    await svc.mark_ready(match.id, u1, NOW + 1)
    match = await svc.mark_ready(match.id, u2, NOW + 2)
    await svc.report_result(match.id, u1, u2, NOW + 3)
    await svc.report_result(match.id, u2, u2, NOW + 4)
    return await svc.confirm_result(match.id, u1, NOW + 5)


def _env() -> tuple[LadderService, Any, FakeEvents]:
    """Fresh ladder stack on the in-memory seams, mocked gateway."""
    db: Any = FakeLadderDatabase()
    events, game_data = FakeEvents(), _game_data()
    svc = LadderService(db, game_data, NullGameGateway(), events)
    return svc, game_data, events


async def _seed(svc: LadderService, ladder_id: str) -> None:
    """Register the canonical Alice/Bob pair."""
    await svc.register_player(ladder_id, "u1", "Alice", now=NOW)
    await svc.register_player(ladder_id, "u2", "Bob", now=NOW)


# ── §9.1 Game independence: full journey on a mocked no-capability gateway ──


@pytest.mark.asyncio
async def test_s91_full_journey_with_mocked_gateway() -> None:
    """inscription → file → match → report manuel → confirmation → rating."""
    svc, game_data, events = _env()
    ladder_id = await _ladder_with_pool(svc, game_data)
    await _seed(svc, ladder_id)

    match = await _full_match(svc, ladder_id)

    assert match.status == "COMPLETED"
    assert match.winner_user_id == "u2"
    assert match.map_snapshot["filename"] in ("arabia.v0", "arena.v0")
    winner = await svc.get_player(ladder_id, "u2")
    loser = await svc.get_player(ladder_id, "u1")
    assert winner is not None and winner.rating > 1000
    assert loser is not None and loser.rating < 1000
    assert winner.wins == 1 and loser.losses == 1
    intents = [i for i, _ in events.intents]
    assert "match.created" in intents
    assert "match.result_confirmed" in intents


@pytest.mark.asyncio
async def test_s91_degraded_gateway_full_manual_path() -> None:
    """Providers down: the domain still completes the journey manually."""
    svc, game_data, _ = _env()
    ladder_id = await _ladder_with_pool(svc, game_data)
    await _seed(svc, ladder_id)

    match = await _full_match(svc, ladder_id)

    assert match.status == "COMPLETED"
    assert match.origin == "matchmaking"
    assert db_collections(svc)[MATCHES_COLLECTION]  # match persisted


# ── §9.2 Platform independence: no platform/game IDs in mod collections ──


@pytest.mark.asyncio
async def test_s92_no_platform_or_game_ids_in_collections() -> None:
    """Mod collections stay free of platform/game identifiers (§9.2)."""
    svc, game_data, _ = _env()
    ladder_id = await _ladder_with_pool(svc, game_data)
    await _seed(svc, ladder_id)
    await _full_match(svc, ladder_id)

    banned_fragments = ("discord", "channel", "message", "https://", "profile_id:")
    for docs in db_collections(svc).values():
        for doc in docs.values():
            blob = repr(doc)
            for fragment in banned_fragments:
                assert fragment not in blob, (fragment, doc)


# ── §9.3/§9.4 Timing and matching properties (full versions in unit tests) ──


@pytest.mark.asyncio
async def test_s93_two_compatible_players_match_in_one_pass() -> None:
    """Δrating ≤ base: matched in a single pass right after the 2nd joins."""
    svc, game_data, _ = _env()
    ladder_id = await _ladder_with_pool(svc, game_data)
    await _seed(svc, ladder_id)
    await svc.join_queue(ladder_id, "u1", NOW, has_game_profile=True)
    await svc.join_queue(ladder_id, "u2", NOW + 1, has_game_profile=True)

    matches = await svc.run_matchmaking_pass(ladder_id, NOW + 2)

    assert len(matches) == 1
    assert matches[0].status == MATCH_STATUS_CREATED


def test_s94_no_pairable_player_left_alone_with_full_matching_available() -> None:
    """Two compatible pairs: the pass never leaves a pairable player alone."""
    entries = [
        QueueEntry(user_id="a", rating=1000, queued_at=NOW),
        QueueEntry(user_id="b", rating=1005, queued_at=NOW),
        QueueEntry(user_id="c", rating=1500, queued_at=NOW),
        QueueEntry(user_id="d", rating=1495, queued_at=NOW),
    ]
    matches = matchmaking_pass(entries, LadderSettingsModel(), now=NOW + 60)
    matched = {frozenset((m.host_user_id, m.guest_user_id)) for m in matches}
    assert len(matches) == 2
    assert frozenset(("a", "b")) in matched and frozenset(("c", "d")) in matched


# ── §9.5 Rating invariants ──


@pytest.mark.asyncio
async def test_s95_rating_invariant_and_single_application() -> None:
    """Sum of deltas == current rating; exactly one rating row per player."""
    svc, game_data, _ = _env()
    ladder_id = await _ladder_with_pool(svc, game_data)
    await _seed(svc, ladder_id)
    match = await _full_match(svc, ladder_id)

    winner = await svc.get_player(ladder_id, match.winner_user_id)
    loser = await svc.get_player(ladder_id, match.loser_user_id)
    assert winner is not None and loser is not None
    db: Any = svc._db
    history_w = await db.find_rating_history(ladder_id, winner.user_id)
    history_l = await db.find_rating_history(ladder_id, loser.user_id)
    assert sum(h["delta"] for h in history_w) == pytest.approx(winner.rating - 1000)
    assert sum(h["delta"] for h in history_l) == pytest.approx(loser.rating - 1000)
    assert len(history_w) == 1 and len(history_l) == 1  # applied exactly once


# ── §9.7 Idempotent transitions ──


@pytest.mark.asyncio
async def test_s97_transitions_idempotent_replay() -> None:
    """Replaying confirm on a COMPLETED match applies no rating side effect."""
    svc, game_data, _ = _env()
    ladder_id = await _ladder_with_pool(svc, game_data)
    await _seed(svc, ladder_id)
    match = await _full_match(svc, ladder_id)
    winner = await svc.get_player(ladder_id, match.winner_user_id)
    assert winner is not None
    rating_after_first = winner.rating

    replayed = await svc.confirm_result(match.id, "u1", NOW + 60)
    assert replayed.status == "COMPLETED"
    winner = await svc.get_player(ladder_id, match.winner_user_id)
    assert winner is not None and winner.rating == rating_after_first
    db: Any = svc._db
    assert len(await db.find_rating_history(ladder_id, winner.user_id)) == 1


# ── §9.8 Non-admin guarantees ──


@pytest.mark.asyncio
async def test_s98_non_admin_cannot_mutate_admin_surfaces() -> None:
    """Only registered players queue; only match participants report."""
    svc, game_data, _ = _env()
    ladder_id = await _ladder_with_pool(svc, game_data)
    await _seed(svc, ladder_id)

    with pytest.raises(NotRegisteredError):
        await svc.join_queue(ladder_id, "intruder", NOW, has_game_profile=True)
    with pytest.raises(LadderError):
        await svc.report_result("match:none", "u1", "u1", NOW)

    await svc.join_queue(ladder_id, "u1", NOW, has_game_profile=True)
    await svc.join_queue(ladder_id, "u2", NOW, has_game_profile=True)
    (match,) = await svc.run_matchmaking_pass(ladder_id, NOW)
    await svc.register_player(ladder_id, "outsider", "Mallory", now=NOW)
    with pytest.raises(LadderError):
        await svc.report_result(match.id, "outsider", "u1", NOW + 1)
