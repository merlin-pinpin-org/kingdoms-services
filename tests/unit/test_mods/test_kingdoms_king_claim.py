"""King-claim flow (drasah 2026-10-11): imposed-mode Kings pick a throne.

In imposed mode the naming DM must never appear — the validated King
claims an existing kingdom instead. Guards:

- the service queues an approved King in ANY season mode
  (``enroll_king_awaiting_kingdom``) — the imposed-mode rejection that
  blocked Drasah's live candidature is gone;
- ``claim_kingdom`` binds a queued King to an available throne and
  refuses Gaïa, refused, already-reigned and non-queued claims;
- the persistent select claims through the live wiring and answers
  with the outcome.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from kingdoms.mods.kingdoms.config import default_season_config
from kingdoms.mods.kingdoms.kingdom_persistent import (
    KingdomKingClaimSelect,
    KingdomsPanelWiring,
    _king_name_strings,
    register_kingdoms_panel_wiring,
)
from kingdoms.mods.kingdoms.models import KingdomValidation, LordRole
from kingdoms.mods.kingdoms.service import (
    KingdomFullError,
    KingdomNotFoundError,
    KingdomsService,
    NotEnrollableError,
    NotQueuedError,
)
from tests.mocks.discord_mock import MockClient, MockGuild, MockInteraction, MockUser
from tests.unit.test_mods.test_kingdoms_service import MemoryStore

pytestmark = pytest.mark.asyncio


def _service() -> KingdomsService:
    return KingdomsService(MemoryStore(), default_season_config())  # type: ignore[arg-type]


async def test_king_can_be_queued_in_imposed_mode() -> None:
    """The regression: an imposed season no longer rejects the King."""
    service = _service()
    await service.launch(["Avalon"])
    lord = await service.enroll_king_awaiting_kingdom("p1", "Arthur")
    assert lord.role is LordRole.KING and lord.in_queue and lord.kingdom_id is None


async def test_claim_kingdom_binds_the_queued_king() -> None:
    service = _service()
    await service.launch(["Avalon"])
    await service.enroll_king_awaiting_kingdom("p1", "Arthur")
    kingdoms = await service.kingdoms()
    throne = next(k for k in kingdoms if not k.is_gaia)
    kingdom = await service.claim_kingdom("p1", throne.id)
    assert kingdom.id == throne.id
    lords = await service.lords()
    king = next(item for item in lords if item.id == "p1")
    assert king.kingdom_id == throne.id and not king.in_queue


async def test_claim_kingdom_refuses_a_taken_throne() -> None:
    service = _service()
    await service.launch(["Avalon"])
    kingdoms = await service.kingdoms()
    throne = next(k for k in kingdoms if not k.is_gaia)
    await service.enroll_king_awaiting_kingdom("p1", "Arthur")
    await service.claim_kingdom("p1", throne.id)
    await service.enroll_king_awaiting_kingdom("p2", "Béatrice")
    with pytest.raises(KingdomFullError):
        await service.claim_kingdom("p2", throne.id)


async def test_claim_kingdom_refuses_gaia_and_refused_kingdoms() -> None:
    service = _service()
    await service.launch(["Avalon"])
    await service.enroll_king_awaiting_kingdom("p1", "Arthur")
    kingdoms = await service.kingdoms()
    gaia = next(k for k in kingdoms if k.is_gaia)
    with pytest.raises(NotEnrollableError):
        await service.claim_kingdom("p1", gaia.id)
    throne = next(k for k in kingdoms if not k.is_gaia)
    refused = await service.refuse_kingdom(throne.id)
    assert refused.validation is KingdomValidation.REFUSED
    with pytest.raises(KingdomNotFoundError):
        await service.claim_kingdom("p1", throne.id)


async def test_claim_kingdom_requires_a_queued_king() -> None:
    service = _service()
    await service.launch(["Avalon"])
    kingdoms = await service.kingdoms()
    throne = next(k for k in kingdoms if not k.is_gaia)
    await service.enroll("p1", "Luc", LordRole.LORD)  # queued Lord, not a King
    with pytest.raises(NotQueuedError):
        await service.claim_kingdom("p1", throne.id)
    await service.enroll_king_awaiting_kingdom("p2", "Arthur")
    await service.claim_kingdom("p2", throne.id)
    with pytest.raises(NotQueuedError):
        await service.claim_kingdom("p2", throne.id)  # already reigns


async def test_available_kingdoms_skip_gaia_and_taken() -> None:
    service = _service()
    await service.launch(["Avalon", "Pictavie"])
    available = await service.available_kingdoms()
    assert sorted(k.name for k in available) == ["Avalon", "Pictavie"]
    throne = next(k for k in available if k.name == "Avalon")
    await service.enroll_king_awaiting_kingdom("p1", "Arthur")
    await service.claim_kingdom("p1", throne.id)
    assert [k.name for k in await service.available_kingdoms()] == ["Pictavie"]


# ------------------------------------------------------------------
# Persistent select (the DM the approved King receives in imposed mode)
# ------------------------------------------------------------------


@dataclass
class FakeLord:
    id: str
    role: str = "king"
    kingdom_id: str | None = None
    left: bool = False
    in_queue: bool = True
    display_name: str = "Arthur"


@dataclass
class FakeSeason:
    imposed_kingdoms: bool = True


@dataclass
class FakeKingdom:
    id: str
    name: str
    validation: str = "pending"
    is_gaia: bool = False


class FakeClaimService:
    """Just the surface the claim flow needs."""

    def __init__(self, kingdoms: list[FakeKingdom]) -> None:
        self._kingdoms = kingdoms
        self.claims: list[tuple[str, str]] = []

    async def current_season(self) -> FakeSeason:
        return FakeSeason()

    async def available_kingdoms(self) -> list[FakeKingdom]:
        return self._kingdoms

    async def claim_kingdom(self, player_id: str, kingdom_id: str) -> FakeKingdom:
        self.claims.append((player_id, kingdom_id))
        return next(k for k in self._kingdoms if k.id == kingdom_id)


def _wire(service: FakeClaimService) -> None:
    register_kingdoms_panel_wiring(KingdomsPanelWiring(kingdoms_service=service))


def _select(service: FakeClaimService) -> KingdomKingClaimSelect:
    strings = _king_name_strings("en")
    return KingdomKingClaimSelect(
        [__import__("discord").SelectOption(label=k.name, value=k.id) for k in service._kingdoms],
        strings["king_claim_placeholder"],
    )


async def test_select_claims_the_picked_throne_and_confirms() -> None:
    service = FakeClaimService([FakeKingdom("k-1", "Avalon")])
    _wire(service)
    select = _select(service)
    select.item._values = ["k-1"]
    guild = MockGuild()
    interaction: Any = MockInteraction(
        user=MockUser(id=111), locale="en-US", client=MockClient(guilds=[guild])
    )
    await select.callback(interaction)
    assert service.claims == [("111", "k-1")]
    assert interaction.followup.messages
    content = interaction.followup.messages[-1].content or ""
    assert "Avalon" in content


async def test_select_reports_the_service_error() -> None:
    class BrokenService(FakeClaimService):
        async def claim_kingdom(self, player_id: str, kingdom_id: str) -> FakeKingdom:
            raise KingdomFullError("taken")

    service = BrokenService([FakeKingdom("k-1", "Avalon")])
    _wire(service)
    select = _select(service)
    select.item._values = ["k-1"]
    interaction: Any = MockInteraction(user=MockUser(id=111), locale="en-US")
    await select.callback(interaction)
    assert interaction.followup.messages
    content = interaction.followup.messages[-1].content or ""
    assert _king_name_strings("en")["king_claim_failed"].format("KingdomFullError") in content
