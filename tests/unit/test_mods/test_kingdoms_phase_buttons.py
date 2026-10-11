"""Admin phase buttons (drasah 2026-10-11): Back-to-setup & End-season.

Drasah's test loop: the admin walks the season phase machine without
waiting for the real calendar. Guards:

- ``set_phase`` moves the season to any phase without touching data;
- Back-to-setup archives + wipes the data, relaunches a fresh season
  in ``setup`` and rebuilds the salons (no stale reposts anymore);
- End-season closes the Conquest (territory counts decide) and moves
  the season to ``ended``;
- the claim flow refreshes everything it touches (stale-view bug).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from kingdoms.mods.kingdoms.config import default_season_config
from kingdoms.mods.kingdoms.kingdom_persistent import (
    KingdomsPanelWiring,
    _after_claim_refresh,
    register_kingdoms_panel_wiring,
)
from kingdoms.mods.kingdoms.service import KingdomsService
from tests.mocks.discord_mock import MockGuild, MockInteraction, MockUser
from tests.unit.test_mods.test_kingdoms_service import MemoryStore

pytestmark = pytest.mark.asyncio


def _service() -> KingdomsService:
    return KingdomsService(MemoryStore(), default_season_config())  # type: ignore[arg-type]


async def test_set_phase_moves_the_season_without_touching_data() -> None:
    service = _service()
    await service.launch(["Avalon"])
    await service.start_season()
    kingdoms = await service.kingdoms()
    assert [k.name for k in kingdoms if not k.is_gaia] == ["Avalon"]

    season = await service.set_phase("ended")
    assert season.phase == "ended"
    kingdoms_after = await service.kingdoms()
    assert [k.name for k in kingdoms_after if not k.is_gaia] == ["Avalon"]  # data survives


# ------------------------------------------------------------------
# Back-to-setup & End-season runners
# ------------------------------------------------------------------


@dataclass
class FakeAdminService:
    """Records the service calls the admin runners make."""

    reset_calls: int = 0
    launch_calls: int = 0
    phase_calls: list[str] = field(default_factory=list)

    async def reset(self) -> None:
        self.reset_calls += 1

    async def launch(self) -> None:
        self.launch_calls += 1

    async def set_phase(self, phase: str) -> None:
        self.phase_calls.append(phase)


def _admin_interaction(guild: MockGuild) -> Any:
    member = MockUser(id=999)
    return MockInteraction(user=member, locale="fr", guild=guild)


async def test_back_to_setup_wipes_and_relaunches(monkeypatch: pytest.MonkeyPatch) -> None:
    from kingdoms.mods.kingdoms import kingdom_persistent as kp

    service = FakeAdminService()
    register_kingdoms_panel_wiring(
        KingdomsPanelWiring(kingdoms_service=service, bot_admins=("999",))
    )
    purged: list[str] = []

    async def fake_purge(guild: Any) -> tuple[bool, int]:
        purged.append("done")
        return True, 12

    async def fake_status(guild: Any, locale: str) -> None:
        return None

    monkeypatch.setattr(kp, "_purge_and_reinstall", fake_purge)
    monkeypatch.setattr(kp, "_refresh_season_status_safe", fake_status)
    guild = MockGuild()
    interaction = _admin_interaction(guild)
    strings = kp._profile_strings("fr")
    await kp._run_back_to_setup(interaction, strings)

    assert service.reset_calls == 1 and service.launch_calls == 1
    assert purged == ["done"]
    assert interaction.followup.messages
    assert "setup" in (interaction.followup.messages[-1].content or "")


async def test_end_season_closes_the_conquest(monkeypatch: pytest.MonkeyPatch) -> None:
    from kingdoms.mods.kingdoms import kingdom_persistent as kp

    service = FakeAdminService()
    register_kingdoms_panel_wiring(
        KingdomsPanelWiring(kingdoms_service=service, bot_admins=("999",), territories_service=None)
    )

    async def fake_status(guild: Any, locale: str) -> None:
        return None

    monkeypatch.setattr(kp, "_refresh_season_status_safe", fake_status)
    guild = MockGuild()
    interaction = _admin_interaction(guild)
    strings = kp._profile_strings("fr")
    await kp._run_end_season(interaction, strings)

    assert service.phase_calls == ["ended"]
    assert interaction.followup.messages


# ------------------------------------------------------------------
# Claim refresh (stale-view bug)
# ------------------------------------------------------------------


@dataclass
class _ClaimKingdom:
    id: str = "k-1"
    name: str = "Avalon"


async def test_after_claim_refresh_touches_every_surface(monkeypatch: pytest.MonkeyPatch) -> None:
    from kingdoms.mods.kingdoms import kingdom_persistent as kp

    service = FakeAdminService()
    register_kingdoms_panel_wiring(KingdomsPanelWiring(kingdoms_service=service))
    called: list[str] = []

    async def fake_structures(guild: Any, service: Any) -> None:
        called.append("overwrites")

    async def fake_views(guild: Any, kingdom_id: str) -> None:
        called.append(f"views:{kingdom_id}")

    async def fake_status(guild: Any, locale: str) -> None:
        called.append("status")

    async def fake_panel(guild: Any, locale: str) -> None:
        called.append("panel")

    async def fake_announce(guild: Any, locale: str, who: str, kingdom: str, king: bool) -> None:
        called.append(f"announce:{kingdom}")

    monkeypatch.setattr(kp, "_refresh_realm_views_for_kingdom_safe", fake_views)
    monkeypatch.setattr(kp, "_refresh_season_status_safe", fake_status)
    monkeypatch.setattr(kp, "_refresh_realms_panel_safe", fake_panel)
    monkeypatch.setattr(kp, "_announce_enrollment_safe", fake_announce)

    import kingdoms.mods.kingdoms.kingdom_realms as realms

    async def fake_ensure(guild: Any, service: Any) -> None:
        called.append("structures")

    monkeypatch.setattr(realms, "ensure_all_realm_structures", fake_ensure)

    await _after_claim_refresh(MockGuild(), "fr", _ClaimKingdom(), "42")

    assert "structures" in called
    assert "views:k-1" in called
    assert "status" in called
    assert "panel" in called
    assert "announce:Avalon" in called
