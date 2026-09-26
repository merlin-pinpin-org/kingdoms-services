"""Tests for the enrollment workflow screen (kingdoms-services#115).

The screen shape: step sections, runtime-guarded admin actions, and
the not-yet-built steps rendered as disabled buttons.
"""

from __future__ import annotations

import discord

from kingdoms.discord.enrollment import build_enrollment_view
from tests.mocks.discord_mock import MockGuild, MockInteraction, MockUser


def _walk_buttons(view: discord.ui.LayoutView) -> list[discord.ui.Button]:
    """Collect every button of a layout, whatever the nesting."""
    buttons: list[discord.ui.Button] = []

    def walk(item: object) -> None:
        if isinstance(item, discord.ui.Button):
            buttons.append(item)
        for child in getattr(item, "children", []) or []:
            walk(child)

    for child in view.children:
        walk(child)
    return buttons




def test_enrollment_screen_renders_steps_and_actions() -> None:
    view = build_enrollment_view((), None)
    buttons = _walk_buttons(view)
    labels = [b.label for b in buttons if b.label]
    assert any("Open enrollment" in label for label in labels)
    assert any("bot-admins role" in label for label in labels)
    disabled = [b for b in buttons if b.disabled]
    assert {b.label for b in disabled if b.label} == {"🎮 Select game", "🧩 Configure mod"}


def test_enrollment_screen_custom_ids_follow_convention() -> None:
    view = build_enrollment_view((), None)
    for button in _walk_buttons(view):
        assert button.custom_id is not None
        assert button.custom_id.startswith("enrollment:")


async def test_guarded_actions_validate_at_click_time() -> None:
    """A member without privileges clicks: denied, state unchanged (the mandate)."""
    view = build_enrollment_view(("1",), None)
    buttons = {b.label: b for b in _walk_buttons(view) if b.label}
    open_button = next(b for b in buttons.values() if "Open" in (b.label or ""))
    interaction = MockInteraction(user=MockUser(id=99), guild=MockGuild(id=1))
    interaction.guild_id = 1
    if open_button.callback is not None:
        await open_button.callback(interaction)
    assert interaction.response.sent is True
    assert "not allowed" in (interaction.response.message or "").content
