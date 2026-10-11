"""Kingdoms leave flow — the 2026-10-11 staleness regression lot.

Drasah's live reports: (1) the leave approval was cosmetic — the data
never recorded the departure, so the throne stayed taken and the
Héritiers du trône claim flow could never fire; (2) the scheduler moved
the game (cycle/age switches) without re-rendering the salon state
views, so every panel went stale.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from kingdoms.mods.kingdoms.service import KING_ROLE, KingdomsService

from .test_kingdoms_events import MemoryStore, _config


class _FakeMemberlessGuild:
    """A guild where every member lookup fails (the DM paths bail out)."""

    id = "1"
    text_channels: ClassVar[list[Any]] = []
    voice_channels: ClassVar[list[Any]] = []
    categories: ClassVar[list[Any]] = []

    def get_member(self, _user_id: int) -> None:
        return None

    async def fetch_member(self, _user_id: int) -> None:
        raise LookupError("no member")


@pytest.fixture()
def service() -> KingdomsService:
    return KingdomsService(MemoryStore(), _config())


async def test_heir_claims_the_freed_throne(service: KingdomsService) -> None:
    """The full Héritiers du trône chain: a leave frees the throne."""
    await service.launch(["Aquitaine"])
    await service.enroll_king_awaiting_kingdom("king-a", "King A")
    first = await service.assign("king-a", "Aquitaine", KING_ROLE)
    assert first.kingdom_id == "k-1"
    heir = await service.enroll_king_awaiting_kingdom("heir-1", "Héritier")
    assert heir.in_queue is True

    # the throne is taken: nothing to claim
    assert await service.available_kingdoms() == []

    await service.leave("king-a", "gone")
    available = await service.available_kingdoms()
    assert [k.name for k in available] == ["Aquitaine"]

    claimed = await service.claim_kingdom("heir-1", "k-1")
    assert claimed.name == "Aquitaine"
    lords = {item.id: item for item in await service.lords()}
    assert lords["heir-1"].in_queue is False
    assert lords["heir-1"].kingdom_id == "k-1"


async def test_apply_player_leave_records_the_departure(service: KingdomsService) -> None:
    """The admin/eject leave now writes the data + pings the heirs."""
    from kingdoms.mods.kingdoms.kingdom_persistent import (
        KingdomsPanelWiring,
        _apply_player_leave_safe,
        register_kingdoms_panel_wiring,
    )

    register_kingdoms_panel_wiring(KingdomsPanelWiring(kingdoms_service=service))
    await service.launch(["Aquitaine"])
    await service.enroll_king_awaiting_kingdom("king-a", "King A")
    await service.assign("king-a", "Aquitaine", KING_ROLE)
    await service.enroll_king_awaiting_kingdom("heir-1", "Héritier")

    pinged: list[str] = []

    async def _fake_claim_request(
        guild: Any, applicant_id: str, locale: str, kingdoms: list[Any]
    ) -> None:
        pinged.append(applicant_id)

    import kingdoms.mods.kingdoms.kingdom_persistent as persistent

    original = persistent._send_king_claim_request
    persistent._send_king_claim_request = _fake_claim_request  # type: ignore[assignment]
    try:
        guild = _FakeMemberlessGuild()
        applied = await _apply_player_leave_safe(guild, "king-a", "admin eject", "fr")
        assert applied is True
    finally:
        persistent._send_king_claim_request = original  # type: ignore[assignment]

    lord = next(item for item in await service.lords() if item.id == "king-a")
    assert lord.left is True
    assert pinged == ["heir-1"]


async def test_scheduler_refreshes_the_views_after_due_slots(monkeypatch: Any) -> None:
    """A replayed slot moves the game with no click — the views re-render."""
    import asyncio

    import kingdoms.mods.kingdoms.kingdom_persistent as persistent
    from kingdoms.mods.kingdoms.kingdom_events_loop import (
        start_kingdoms_event_scheduler,
        stop_kingdoms_event_scheduler,
    )

    from .test_kingdoms_events_loop import (
        _FakeChannel,
        _FakeEvents,
        _FakeGuild,
        _FakeKingdoms,
        _FakeLogs,
        _FakeSeason,
    )

    refreshed: list[str] = []

    async def _fake_refresh(guild: Any, locale: str) -> None:
        refreshed.append(str(guild.id))

    monkeypatch.setattr(persistent, "_refresh_all_realm_views_safe", _fake_refresh)
    await stop_kingdoms_event_scheduler()

    class _Bot:
        def __init__(self) -> None:
            self.guilds = [_FakeGuild(_FakeChannel())]
            self.kingdoms_events_service = _FakeEvents([{"kind": "cycle", "cycle": 2}])
            self.kingdoms_service = _FakeKingdoms(_FakeSeason())
            self.kingdoms_time_service = None
            self.logs_service = _FakeLogs()

    bot = _Bot()
    assert start_kingdoms_event_scheduler(bot, tick_seconds=0) is True
    for _ in range(30):
        await asyncio.sleep(0)
        if refreshed:
            break
    await stop_kingdoms_event_scheduler()
    assert refreshed, "the scheduler must refresh the views after a replayed slot"
