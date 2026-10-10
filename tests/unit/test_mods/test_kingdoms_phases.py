"""The season phases (Drasah's game-design rule, 2026-10-10) — unit tests.

Nothing random is revealed before the admin starts the game: the
launch lands in ``setup`` (no civilization draft, no territory draw,
placeholders in Territoire/Alliances), the « 🎯 Démarrer la saison »
button reveals everything at once, and the season mode (free/imposed)
is manageable mid-season without wiping anything.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from kingdoms.mods.kingdoms.config import default_season_config
from kingdoms.mods.kingdoms.service import KingdomsService
from tests.unit.test_mods.test_kingdoms_service import MemoryStore

pytestmark = pytest.mark.asyncio


@dataclass
class _Season:
    phase: str = "setup"
    imposed_kingdoms: bool = False
    id: str = "s-test"


class _KingdomsService:
    """The minimal kingdoms surface the phase flows need."""

    def __init__(self, phase: str = "setup", imposed: bool = False) -> None:
        self._season = _Season(phase, imposed)
        self.started = 0
        self.mode_calls: list[bool] = []

    async def current_season(self) -> _Season:
        return self._season

    async def kingdoms(self) -> list[Any]:
        return []

    async def lords(self) -> list[Any]:
        return []

    async def start_season(self) -> _Season:
        self.started += 1
        self._season.phase = "started"
        return self._season

    async def set_imposed_mode(self, imposed: bool) -> _Season:
        self.mode_calls.append(bool(imposed))
        self._season.imposed_kingdoms = bool(imposed)
        return self._season


@dataclass
class _TerritoriesService:
    drawn: list[str] = field(default_factory=list)
    existing: list[Any] = field(default_factory=list)

    async def territories(self) -> list[Any]:
        return self.existing

    async def draw_initial(self) -> None:
        self.drawn.append("all")

    async def draw_initial_for(self, kingdom_id: str) -> None:
        self.drawn.append(kingdom_id)


def _service() -> KingdomsService:
    return KingdomsService(MemoryStore(), default_season_config())  # type: ignore[arg-type]


# ---------------------------------------------------------------- service


async def test_launch_lands_in_setup_with_no_draft() -> None:
    service = _service()
    season = await service.launch()
    assert season.phase == "setup"
    kingdoms = await service.kingdoms()
    assert all(not k.civilizations for k in kingdoms if not k.is_gaia)


async def test_kingdoms_created_during_setup_never_draw() -> None:
    from kingdoms.mods.kingdoms.models import LordRole

    service = _service()
    await service.launch()
    lord = await service.enroll("king-a", "King A", LordRole.KING, proposed_name="Aquitaine")
    kingdom = next(k for k in await service.kingdoms() if k.name == "Aquitaine")
    assert lord.kingdom_id == kingdom.id
    assert kingdom.civilizations == []
    added = await service.add_kingdom("Bourgogne")
    assert added.civilizations == []


async def test_start_season_reveals_the_draft_for_everyone() -> None:
    service = _service()
    await service.launch(imposed_names=["Aquitaine", "Bourgogne"])
    await service.start_season()
    season = await service.current_season()
    assert season is not None and season.phase == "started"
    drawn = [civ for k in await service.kingdoms() if not k.is_gaia for civ in k.civilizations]
    expected = 2 * service.config.starting_civilizations
    assert len(drawn) == expected
    assert len(set(drawn)) == expected  # no duplicates between kingdoms
    # idempotent: a second click changes nothing
    await service.start_season()
    drawn_again = [civ for k in await service.kingdoms() if not k.is_gaia for civ in k.civilizations]
    assert drawn_again == drawn


async def test_kingdoms_added_after_the_start_draw_immediately() -> None:
    service = _service()
    await service.launch()
    await service.start_season()
    added = await service.add_kingdom("Aquitaine")
    assert len(added.civilizations) == service.config.starting_civilizations


async def test_set_imposed_mode_flips_without_wiping() -> None:
    from kingdoms.mods.kingdoms.models import LordRole

    service = _service()
    await service.launch(imposed_names=["Aquitaine"])
    lord = await service.enroll("lord-a", "Lord A", LordRole.LORD, kingdom_name="Aquitaine")
    season = await service.set_imposed_mode(False)
    assert season.imposed_kingdoms is False
    assert (await service.current_season()) is not None
    lords = await service.lords()
    assert [item.id for item in lords] == [lord.id]  # nothing wiped
    # the free mode unblocks the King enrollment (the drasah incident)
    enrolled = await service.enroll_king_awaiting_name("king-b", "King B")
    assert enrolled.role == "king" and enrolled.in_queue


async def test_legacy_seasons_without_phase_default_to_started() -> None:
    service = _service()
    await service.launch()
    season = await service.current_season()
    assert season is not None
    document = season.to_mongo()
    document.pop("phase", None)
    from kingdoms.mods.kingdoms.models import SeasonState

    legacy = SeasonState.from_mongo(document)
    assert legacy.phase == "started"


# ------------------------------------------------------------- territory


async def test_territory_draw_is_deferred_in_setup_phase() -> None:
    from kingdoms.mods.kingdoms.kingdom_persistent import (
        KingdomsPanelWiring,
        _draw_territories_after_launch_safe,
        _draw_territories_for_kingdom_safe,
        register_kingdoms_panel_wiring,
    )

    territories = _TerritoriesService()
    register_kingdoms_panel_wiring(
        KingdomsPanelWiring(
            kingdoms_service=_KingdomsService(phase="setup"),
            territories_service=territories,
        )
    )
    await _draw_territories_after_launch_safe()
    await _draw_territories_for_kingdom_safe("k-1")
    assert territories.drawn == []

    register_kingdoms_panel_wiring(
        KingdomsPanelWiring(
            kingdoms_service=_KingdomsService(phase="started"),
            territories_service=territories,
        )
    )
    await _draw_territories_after_launch_safe()
    assert territories.drawn == ["all"]


# --------------------------------------------------------- admin buttons


async def test_start_season_button_reveals_and_confirms() -> None:
    import discord

    from kingdoms.mods.kingdoms.kingdom_persistent import (
        KingdomAdminButton,
        KingdomsPanelWiring,
        register_kingdoms_panel_wiring,
    )
    from tests.mocks.discord_mock import MockInteraction, MockUser

    service = _KingdomsService(phase="setup")
    register_kingdoms_panel_wiring(
        KingdomsPanelWiring(kingdoms_service=service, bot_admins=("1",))
    )
    button = KingdomAdminButton("start-season", "🎯 Démarrer la saison", discord.ButtonStyle.success)
    interaction: Any = MockInteraction(user=MockUser(id=1), locale="fr-FR")
    await button.callback(interaction)
    assert service.started == 1
    assert interaction.followup.messages


async def test_season_mode_button_toggles_the_flag() -> None:
    import discord

    from kingdoms.mods.kingdoms.kingdom_persistent import (
        KingdomAdminButton,
        KingdomsPanelWiring,
        register_kingdoms_panel_wiring,
    )
    from tests.mocks.discord_mock import MockInteraction, MockUser

    service = _KingdomsService(phase="setup", imposed=True)
    register_kingdoms_panel_wiring(
        KingdomsPanelWiring(kingdoms_service=service, bot_admins=("1",))
    )
    button = KingdomAdminButton("season-mode", "🔄 Mode de saison", discord.ButtonStyle.secondary)
    interaction: Any = MockInteraction(user=MockUser(id=1), locale="fr-FR")
    await button.callback(interaction)
    assert service.mode_calls == [False]
    content = interaction.followup.messages[-1].content or ""
    assert "libre" in content
