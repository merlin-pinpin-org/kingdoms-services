"""The Paramètres panel — the admin season buttons (phases lot)."""
from __future__ import annotations

import pytest

from kingdoms.mods.kingdoms.kingdom_panels import SETTINGS_PANEL_MARKER, build_settings_panel

pytestmark = pytest.mark.asyncio


def _custom_ids(view) -> set[str]:
    return {
        getattr(item, "custom_id", "")
        for item in view.walk_children()
        if getattr(item, "custom_id", None)
    }


async def test_settings_panel_carries_the_phase_buttons() -> None:
    view = await build_settings_panel(None, "1", "tester")
    ids = _custom_ids(view)
    assert "kingdoms:admin:start-season" in ids
    assert "kingdoms:admin:season-mode" in ids
    # the existing actions survive (nothing broken)
    for action in (
        "launch",
        "status",
        "deploy",
        "sync",
        "reset",
        "assign",
        "add-kingdom",
        "remove",
    ):
        assert f"kingdoms:admin:{action}" in ids
    assert SETTINGS_PANEL_MARKER in str(view.to_components())


async def test_settings_panel_rows_never_overflow() -> None:
    """Discord caps an ActionRow at 5 items — an overflowing row makes
    the whole panel send fail."""
    import discord

    view = await build_settings_panel(None, "1", "tester")
    for item in view.walk_children():
        if isinstance(item, discord.ui.ActionRow):
            assert len(item.children) <= 5


async def test_remove_flow_lists_active_lords() -> None:
    """Drasah's rule (2026-10-11): the 🗑️ eject button shows the active
    roster (assigned or waiting) — no handle to type."""
    from kingdoms.mods.kingdoms.kingdom_persistent import (
        KingdomsPanelWiring,
        _active_lord_options,
        register_kingdoms_panel_wiring,
    )
    from kingdoms.mods.kingdoms.kingdom_profiles import _strings
    from kingdoms.mods.kingdoms.service import KING_ROLE, LORD_ROLE, KingdomsService

    from .test_kingdoms_events import MemoryStore, _config

    service = KingdomsService(MemoryStore(), _config())
    await service.launch([])
    await service.add_kingdom("Aquitaine")
    await service.enroll("king-1", "King Arthur", KING_ROLE, proposed_name="Bretagne")
    await service.enroll("lord-1", "Lord Modal", LORD_ROLE, kingdom_name="Aquitaine")
    await service.enroll("lord-2", "Lord Waiting", LORD_ROLE)  # queue
    register_kingdoms_panel_wiring(KingdomsPanelWiring(kingdoms_service=service))

    strings = _strings("fr")
    options = await _active_lord_options(strings)
    assert options is not None
    labels = {option.value: option for option in options}
    assert set(labels) == {"king-1", "lord-1", "lord-2"}
    assert labels["king-1"].emoji is not None  # the king is crowned
    assert labels["lord-1"].description == "Aquitaine"
    assert labels["lord-2"].description == strings["remove_queue_desc"]


async def test_remove_flow_roster_empty_when_service_gone() -> None:
    from kingdoms.mods.kingdoms.kingdom_persistent import (
        KingdomsPanelWiring,
        _active_lord_options,
        register_kingdoms_panel_wiring,
    )
    from kingdoms.mods.kingdoms.kingdom_profiles import _strings

    register_kingdoms_panel_wiring(KingdomsPanelWiring())
    assert await _active_lord_options(_strings("fr")) is None
