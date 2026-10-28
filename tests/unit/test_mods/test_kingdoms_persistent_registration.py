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


def test_the_five_alliances_buttons_are_registered() -> None:
    """alliances-lisibilite-v1 T2 (Drasah 25/10): the five ephemeral
    answers of the alliances panel must survive a restart."""
    bot = _RecordingBot()

    register_kingdoms_persistent_items(bot)  # type: ignore[arg-type]

    for name in (
        "KingdomAlliancesNextButton",
        "KingdomAlliancesLockedButton",
        "KingdomAlliancesHistoryButton",
        "KingdomAlliancesDurationsButton",
        "KingdomAlliancesNamesButton",
    ):
        assert any(item.__name__ == name for item in bot.registered), name


def test_the_attack_info_button_is_registered() -> None:
    """attaque-ergonomie-v1 (Drasah 28/10): the ⚔️ attack/defense info
    button must survive a restart — it replaced the declaration
    thread of every attack salon."""
    bot = _RecordingBot()

    register_kingdoms_persistent_items(bot)  # type: ignore[arg-type]

    assert any(item.__name__ == "KingdomAttackInfoButton" for item in bot.registered)


def test_the_draft_view_button_is_registered() -> None:
    """blasons-vs-v1 (Drasah 28/10): the 📜 « Voir la draft » button of
    the meeting message must survive a restart."""
    bot = _RecordingBot()

    register_kingdoms_persistent_items(bot)  # type: ignore[arg-type]

    assert any(item.__name__ == "KingdomDraftViewButton" for item in bot.registered)
