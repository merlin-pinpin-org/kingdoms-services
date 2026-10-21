"""The views registry: the declared menus and panels stay coherent."""

from __future__ import annotations

import pytest

from kingdoms.discord.views_declared import declare_views_from_config
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
    declare_views_from_config("config")


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


def test_the_game_admin_menu_declares_its_actions() -> None:
    admin_menu = get_view("menu:game:aoe2:admin")
    assert admin_menu is not None
    actions = {b.action for b in admin_menu.buttons if b.action}
    assert actions == {"import-map", "bulk-import-map", "create-pool"}
    for button in admin_menu.buttons:
        if button.action:
            assert not button.target, "an action button never navigates"


def test_a_button_is_exactly_one_driver() -> None:
    for decl in registered_views():
        for button in decl.buttons:
            drivers = sum(1 for d in (button.target, button.sync, button.action) if d)
            assert drivers == 1, f"{decl.id}: a button is target, sync or action — never {drivers}"


def test_emojis() -> None:
    assert ADMIN_EMOJI == "\U0001f512"
    assert SYNC_EMOJI == "\U0001F527"


@pytest.mark.asyncio
async def test_a_mod_declares_its_own_views(tmp_path) -> None:
    """A mod's views.yaml merges into the registry (kingdoms#179)."""
    import yaml

    (tmp_path / "views.yaml").write_text(
        yaml.safe_dump(
            {"views": {"menu:home": {"kind": "menu", "buttons": [{"target": "menu:mymod:home", "label": "x"}]}}}
        ),
        encoding="utf-8",
    )
    mods = tmp_path / "mods"
    mods.mkdir()
    (mods / "mymod.yaml").write_text(
        yaml.safe_dump({"id": "mymod", "enabled": True, "seasonal": False}), encoding="utf-8"
    )
    (mods / "mymod.views.yaml").write_text(
        yaml.safe_dump({"views": {"menu:mymod:home": {"kind": "menu", "buttons": []}}}), encoding="utf-8"
    )

    clear_registry()
    from kingdoms.discord.views_declared import declare_views_from_config

    declare_views_from_config(tmp_path)
    assert get_view("menu:mymod:home") is not None, "the mod's view merged into the registry"
    assert not validate_registry(), "the cross-references validate both ways"


@pytest.mark.asyncio
async def test_the_menu_builder_honors_the_pin_builder_contract() -> None:
    """The declared menu builder takes the guild_id at build time.

    The static-pin machinery calls ``build_layout(guild_id)`` — a
    builder closing over the wrong arity broke every pinned menu on
    the test env (2026-10-20): this test pins the contract.
    """
    from unittest.mock import MagicMock

    from kingdoms.discord.views_routing import build_declared_menu

    builder = build_declared_menu(MagicMock(), "menu:home")
    layout = await builder("42")
    customs = [c.custom_id for c in layout.walk_children() if getattr(c, "custom_id", None)]
    assert any(c == "view:open:panel:profile" for c in customs), "the navigation buttons render"


@pytest.mark.asyncio
async def test_open_declared_view_answers_an_unwired_menu() -> None:
    """A declared menu opens without a handler (the router builds it).

    The 2026-10-20 live bug: every menu button answered "unknown view"
    because only the panels were wired. Menus navigate — the router
    builds them from the registry; only an undeclared id answers
    unknown_view.
    """

    class _Response:
        def __init__(self) -> None:
            self.sent_message = False
            self.deferred = False

        async def defer(self, ephemeral: bool = False, thinking: bool = False) -> None:
            self.deferred = True

        async def send_message(self, *args: object, **kwargs: object) -> None:
            self.sent_message = True

    class _Followup:
        def __init__(self) -> None:
            self.sent: list[object] = []

        async def send(self, view: object = None, ephemeral: bool = False, **kwargs: object) -> None:
            self.sent.append(view)

    class _Inter:
        guild_id = 42
        client = None
        response = _Response()
        followup = _Followup()

    from kingdoms.discord.views_routing import open_declared_view

    inter = _Inter()
    await open_declared_view(inter, "menu:games:home")  # type: ignore[arg-type]
    assert inter.followup.sent, "the menu answered with its layout"

    unknown = _Inter()
    await open_declared_view(unknown, "menu:not:declared")  # type: ignore[arg-type]
    assert unknown.response.sent_message, "an undeclared id answers unknown_view"


def test_admin_menus_carry_a_scope_notice() -> None:
    """The admin surfaces resolve a tree scope (their channels/roles notice)."""
    for view_id in ("menu:admin", "menu:games:admin", "menu:game:aoe2:admin", "menu:mods:admin"):
        decl = get_view(view_id)
        assert decl is not None
        tree_scope = decl.scope.split("/general")[0].split("/admin")[0]
        assert tree_scope, f"{view_id}: an admin surface renders its scope's notice"


def test_scope_surfaces_registry_runs_the_scope_and_its_descendants() -> None:
    """The sync button discovers the registered syncs by scope prefix."""
    import asyncio

    from kingdoms.discord.scope_surfaces import (
        clear_scope_syncs,
        register_scope_sync,
        run_scope_syncs,
    )

    clear_scope_syncs()
    ran: list[str] = []

    async def _games(guild_id: str, bot: object) -> int:
        ran.append("games")
        return 1

    async def _aoe2(guild_id: str, bot: object) -> int:
        ran.append("aoe2")
        return 2

    async def _mods(guild_id: str, bot: object) -> int:
        ran.append("mods")
        return 4

    register_scope_sync("games", _games)
    register_scope_sync("games/aoe2", _aoe2)
    register_scope_sync("mods", _mods)

    total = asyncio.run(run_scope_syncs("1", None, "games/aoe2"))
    assert sorted(ran) == ["aoe2", "games"]
    assert total == 3

    ran.clear()
    total = asyncio.run(run_scope_syncs("1", None, "mods"))
    assert ran == ["mods"]
    clear_scope_syncs()
