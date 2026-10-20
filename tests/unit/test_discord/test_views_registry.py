"""The views registry: the declared menus and panels stay coherent."""

from __future__ import annotations

import pytest

from kingdoms.discord.views_declared import declare_platform_views
from kingdoms.discord.views_registry import (
    ADMIN_EMOJI,
    SYNC_EMOJI,
    clear_registry,
    get_view,
    registered_views,
    validate_registry,
)


@pytest.fixture(autouse=True)
def _fresh_registry() -> None:
    clear_registry()
    declare_platform_views()


def test_the_platform_views_are_declared() -> None:
    expected = {
        "menu:home",
        "panel:profile",
        "menu:admin",
        "panel:status",
        "menu:games:home",
        "menu:games:admin",
        "menu:games:aoe2",
        "menu:game:aoe2:admin",
        "panel:game:aoe2:live",
        "panel:game:aoe2:players",
        "menu:mods:home",
        "menu:mods:admin",
    }
    declared = {v.id for v in registered_views()}
    missing = expected - declared
    assert not missing, f"views declared in the doc but not in code: {missing}"


def test_every_button_target_is_declared() -> None:
    drifts = validate_registry()
    assert not drifts, f"the views registry drifted: {drifts}"


def test_home_opens_the_four_sections() -> None:
    home = get_view("menu:home")
    assert home is not None
    targets = {b.target for b in home.buttons}
    assert targets == {"panel:profile", "menu:games:home", "menu:mods:home", "menu:admin"}


def test_admin_buttons_carry_the_padlock() -> None:
    for decl in registered_views():
        for button in decl.buttons:
            if button.admin:
                assert button.label_key, f"{decl.id}: an admin button needs a label"


def test_sync_buttons_exist_only_in_admin_menus() -> None:
    for decl in registered_views():
        for button in decl.buttons:
            if button.sync:
                assert decl.id.endswith(":admin"), f"{decl.id}: sync belongs to admin menus"
                assert not button.target, f"{decl.id}: a sync button never navigates"


def test_the_placeholder_mods_are_disabled() -> None:
    mods_home = get_view("menu:mods:home")
    assert mods_home is not None
    placeholders = [b for b in mods_home.buttons if b.disabled]
    assert len(placeholders) == 3, "ladder, tournament, kingdom: the three placeholders"


def test_emojis() -> None:
    assert ADMIN_EMOJI == "\U0001f512"
    assert SYNC_EMOJI == "\u2699\ufe0f"
