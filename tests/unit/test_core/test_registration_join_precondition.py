"""End-to-end test: registration binding drives the ladder join precondition (#133).

Acceptance criteria of kingdoms-services#133: the "at least one linked
game profile" precondition is enforced through the registration stack
(game-validated seam -> RegistrationService.has_any_profile -> ladder
join), never by a raw database lookup.
"""

from __future__ import annotations

import pytest

from kingdoms.core.services.registration import InvalidProfileError, RegistrationService
from kingdoms.mods.ladder.service import LadderService, NullGameGateway
from kingdoms.mods.ladder.surface import ACTION_JOIN_QUEUE, LadderSurface
from tests.unit.test_core.test_game_data import FakeAudit
from tests.unit.test_core.test_registration import FakeEvents, FakeProfileSeam, FakeRegistrationDatabase
from tests.unit.test_ladder.test_domain import GAME, NOW, OWNER, FakeLadderDatabase, _game_data, _seed_pair
from tests.unit.test_ladder.test_domain import FakeEvents as LadderEvents


async def _bound_service(user_id: str, profile_id: str, valid: set[str]) -> RegistrationService:
    db = FakeRegistrationDatabase()
    seam = FakeProfileSeam(valid)
    svc = RegistrationService(db, {"aoe2": seam}, FakeEvents(), FakeAudit())
    await svc.bind_profile(user_id, "aoe2", profile_id)
    return svc


@pytest.mark.asyncio
async def test_join_requires_a_game_validated_binding() -> None:
    """A user bound through the game seam joins; an unbound user is refused."""
    registration = await _bound_service("u1", "123456", {"123456"})
    db, events, game_data = FakeLadderDatabase(), LadderEvents(), _game_data()
    svc = LadderService(db, game_data, NullGameGateway(), events)
    surface = LadderSurface(svc)
    ladder = await svc.create_ladder(OWNER, "L1", GAME, now=NOW)
    m = await game_data.create_map(GAME, "Arabia", filename="arabia.v0")
    m2 = await game_data.create_map(GAME, "Arena", filename="arena.v0")
    pool = await game_data.create_map_pool(GAME, "P", map_ids=(m.id, m2.id))
    await svc.set_active_pool(ladder.id, pool.id)
    await _seed_pair(svc, ladder.id)

    joined = await surface.execute(
        ACTION_JOIN_QUEUE, ladder.id, "u1", NOW, has_game_profile=await registration.has_any_profile("u1")
    )
    assert joined.ok, joined.reason

    refused = await surface.execute(
        ACTION_JOIN_QUEUE, ladder.id, "u2", NOW, has_game_profile=await registration.has_any_profile("u2")
    )
    assert not refused.ok
    assert "no resolvable game profile" in refused.reason


@pytest.mark.asyncio
async def test_invalid_profile_cannot_reach_the_queue() -> None:
    """A profile the game seam rejects never produces a binding, so no join."""
    registration = RegistrationService(
        FakeRegistrationDatabase(), {"aoe2": FakeProfileSeam({"123456"})}, FakeEvents(), FakeAudit()
    )
    with pytest.raises(InvalidProfileError):
        await registration.bind_profile("u1", "aoe2", "999999")
    assert await registration.has_any_profile("u1") is False

    db, events, game_data = FakeLadderDatabase(), LadderEvents(), _game_data()
    svc = LadderService(db, game_data, NullGameGateway(), events)
    surface = LadderSurface(svc)
    ladder = await svc.create_ladder(OWNER, "L1", GAME, now=NOW)
    m = await game_data.create_map(GAME, "Arabia", filename="arabia.v0")
    m2 = await game_data.create_map(GAME, "Arena", filename="arena.v0")
    pool = await game_data.create_map_pool(GAME, "P", map_ids=(m.id, m2.id))
    await svc.set_active_pool(ladder.id, pool.id)
    await _seed_pair(svc, ladder.id)

    result = await surface.execute(
        ACTION_JOIN_QUEUE, ladder.id, "u1", NOW, has_game_profile=await registration.has_any_profile("u1")
    )
    assert not result.ok
