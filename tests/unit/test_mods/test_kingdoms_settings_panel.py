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
