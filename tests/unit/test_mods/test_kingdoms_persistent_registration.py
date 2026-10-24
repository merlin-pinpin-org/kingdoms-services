"""Startup registration of the persistent Kingdoms items (24/10).

Regression guard: a stray import inside
``register_kingdoms_persistent_items`` (KingdomTavernDrinkButton
imported from the distributor panel) raised ImportError at STARTUP
only — the setup hook swallows it as best-effort, so the bot silently
booted with ZERO persistent views (no tavern button, no attack panel,
no admin buttons) and every panel interaction went unanswered. This
test fails the CI before that class of bug can ship.
"""

from __future__ import annotations

from kingdoms.mods.kingdoms.kingdom_persistent import register_kingdoms_persistent_items


class _RecordingBot:
    """The registration seam only needs ``add_dynamic_items``."""

    def __init__(self) -> None:
        self.registered: list[type] = []

    def add_dynamic_items(self, *classes: type) -> None:
        self.registered.extend(classes)


def test_registration_imports_every_panel_without_error() -> None:
    bot = _RecordingBot()

    register_kingdoms_persistent_items(bot)  # type: ignore[arg-type]

    assert len(bot.registered) >= 25  # the full persistent catalogue registered


def test_the_tavern_button_is_registered() -> None:
    bot = _RecordingBot()

    register_kingdoms_persistent_items(bot)  # type: ignore[arg-type]

    assert any(item.__name__ == "KingdomTavernDrinkButton" for item in bot.registered)
