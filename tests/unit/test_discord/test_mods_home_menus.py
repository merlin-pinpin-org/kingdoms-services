"""The mods/games home and admin menus: the order constraint, the emojis.

The SDK ORDER CONSTRAINT (#393) and the merlin directions (2026-11-13):
the repair button closes an admin row (ALWAYS last, whatever the
declaration or the injection order), the games home carries no door
buttons (the body's notice owns the links), and the kingdom entries
and titles carry the declaration's crown emoji (#334: the visual
identity is data).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import botforge.discord.wiring as wiring
from botforge.discord.views_declared import declare_platform_views


class _InstalledAccess:
    """Every game and mod is installed (the entries all render)."""

    async def enabled_games(self, guild_id: str) -> tuple[str, ...]:
        return ("aoe2",)

    async def enabled_mods(self, guild_id: str) -> tuple[str, ...]:
        return ("kingdom",)


@pytest.fixture()
def rendered_menus(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Render the real declared menus with the kingdom mod installed."""
    from types import SimpleNamespace

    declare_platform_views("config")
    from botforge.core.services.mod_registry import ModRegistry, load_mod_definitions
    from botforge.discord.mod_views import declare_mod_views
    from botforge.discord.views_routing import build_declared_menu

    definitions = load_mod_definitions(Path("config"), repo_root=Path("."))
    # the REAL declaration ships enabled: false (it flips at the skeleton
    # #366, when the code moves in) — the render tests force it on: the
    # menus' contract is what is under test, not the enablement state
    kingdom = definitions.get("kingdom")
    if kingdom is not None:
        object.__setattr__(kingdom, "enabled", True) if not kingdom.enabled else None
    registry = ModRegistry(definitions)
    declare_mod_views(registry)

    monkeypatch.setattr(wiring, "build_guild_access_service", lambda: _InstalledAccess())
    monkeypatch.setattr(wiring, "_GUILD_ACCESS_PLATFORM", ("aoe2",))
    monkeypatch.setattr(wiring, "_GUILD_ACCESS_MODS", ("kingdom",))
    # the doors' channel-id seam: stubbed — the render must never touch
    # the real database from a unit test
    import botforge.discord.ui.panels_provisioning as panels_provisioning

    async def _fake_stored_channel_id(guild_id: str, scope: str, key: str) -> str:
        return f"77{scope.replace('/', '')}{key}"[:19]

    monkeypatch.setattr(panels_provisioning, "stored_channel_id", _fake_stored_channel_id)

    bot = SimpleNamespace(registry=registry, config=SimpleNamespace(config_dir="config"), message_registry=None)

    async def _render(view_id: str) -> list[dict[str, Any]]:
        layout = await build_declared_menu(bot, view_id)("42")
        buttons: list[dict[str, Any]] = []
        texts: list[str] = []
        for container in layout.to_components():
            for item in container.get("components", []):
                if item.get("type") == 2:
                    buttons.append(item)
                elif item.get("type") == 10:
                    texts.append(item.get("content", ""))
        return [{"buttons": buttons, "texts": texts}]

    import asyncio

    view_ids = ("menu:mods:home", "menu:mods:admin", "menu:games:home", "menu:mod:kingdom", "menu:mod:kingdom:admin")
    rendered: dict[str, Any] = {}
    results = asyncio.run(_render_all(_render, view_ids))
    for view_id, payload in zip(view_ids, results, strict=True):
        rendered[view_id] = payload[0]
    return rendered


async def _render_all(render: Any, view_ids: tuple[str, ...]) -> list[list[dict[str, Any]]]:
    return [await render(view_id) for view_id in view_ids]


def test_the_repair_button_closes_the_mods_admin_row(rendered_menus: dict[str, Any]) -> None:
    """The operator's tool is ALWAYS the row's last button (#393)."""
    buttons = rendered_menus["menu:mods:admin"]["buttons"]
    labels = [str(b.get("label")) for b in buttons]
    assert "Kingdom" in labels, labels
    assert labels[-1].lower().startswith("repair"), f"the repair closes the row — got {labels}"


def test_the_kingdom_entries_carry_the_declaration_emoji(rendered_menus: dict[str, Any]) -> None:
    """The auto-injected kingdom buttons carry the declaration's crown (#334)."""
    for view_id in ("menu:mods:home", "menu:mods:admin"):
        buttons = rendered_menus[view_id]["buttons"]
        kingdom = next(b for b in buttons if str(b.get("label")) == "Kingdom")
        assert (kingdom.get("emoji") or {}).get("name") == "\U0001F451", (
            f"{view_id}: the kingdom entry must carry the declaration's crown"
        )


def test_the_kingdom_home_titles_carry_the_crown(rendered_menus: dict[str, Any]) -> None:
    """The kingdom home and admin titles carry the crown (the games' precedent)."""
    for view_id in ("menu:mod:kingdom", "menu:mod:kingdom:admin"):
        header = rendered_menus[view_id]["texts"][0]
        assert "\U0001F451" in header, f"{view_id}: the crown must ride the title — got {header!r}"


def test_the_games_home_carries_no_door_buttons(rendered_menus: dict[str, Any]) -> None:
    """The games home's doors live in the body's notice, not link buttons."""
    buttons = rendered_menus["menu:games:home"]["buttons"]
    assert all(b.get("url") is None for b in buttons), "the games home carries link buttons"
