"""The map's admin menu IS the map card — the body never disappears.

The 2026-11-07 merlin report: clicking Admin on a map's post replaced
the whole message with a bare title-and-buttons menu. The pools had
already moved to "the menu is the card, only the action row swaps" —
this holds the maps to the same contract, through the real render path
(the loopback game service, the real map_post_render).
"""

from __future__ import annotations

from typing import Any

import discord
import pytest

from tests.integration.test_catalog_gateway import _FakeDB
from tests.integration.test_civs_sync_journey import _RegistryFake, _reset_caches, _restore_caches


class _CatalogFake(_FakeDB):
    """The catalog fake, extended to serve the map list."""


async def _start_server(catalog: Any) -> tuple[Any, int]:
    import grpc

    from kingdoms.game_aoe2.rpc import GameDataServicer, add_gamedata_servicer

    server = grpc.aio.server()
    add_gamedata_servicer(server, GameDataServicer(aggregator=None, catalog=catalog))
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    return server, port


class _BotStub:
    """The bot surface the render path touches (no services wired)."""

    logs_service = None

    def __init__(self) -> None:
        self.guilds: list[Any] = []


def _walk(view: discord.ui.LayoutView) -> tuple[list[str], list[str]]:
    """Collect the text contents and the button custom_ids of a layout."""
    texts: list[str] = []
    buttons: list[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, discord.ui.DynamicItem):
            item = item.item
        if isinstance(item, discord.ui.TextDisplay):
            texts.append(str(item.content))
        if isinstance(item, discord.ui.Button) and item.custom_id:
            buttons.append(str(item.custom_id))
        for child in getattr(item, "children", ()) or ():
            visit(child)

    for child in view.children:
        visit(child)
    return texts, buttons


@pytest.mark.asyncio
async def test_the_map_admin_menu_keeps_the_card_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """The menu renders the map's post shape; only the action row swaps."""
    import os

    from kingdoms.core.services.faction_content import FactionContentService
    from kingdoms.core.services.game_data import GameDataService
    from kingdoms.discord.entity_admin_menu import build_entity_admin_menu
    from kingdoms.game_aoe2.server import _CatalogGateway

    catalog_fake = _CatalogFake()
    catalog = _CatalogGateway(
        GameDataService(catalog_fake), FactionContentService(_RegistryFake())
    )
    entry = await catalog._service.create_map(
        "aoe2", "Arabia", filename="arabia", description="The classic"
    )
    server, port = await _start_server(catalog)
    monkeypatch.setenv("GAME_AOE2_URI", f"127.0.0.1:{port}")
    import kingdoms.core.models.db as db_module

    monkeypatch.setattr(db_module, "get_async_database", lambda: _RegistryFake())

    saved = _reset_caches()
    try:
        bot = _BotStub()
        view = await build_entity_admin_menu(
            "map", entry.id, "aoe2", owned=True, guild_id="123", bot=bot
        )
        texts, buttons = _walk(view)
        body = "\n".join(texts)
        assert "Arabia" in body, "the menu carries the map's card, not a bare title"
        assert "The classic" in body, "the description stays (the body never disappears)"
        assert any(entry.id in line for line in texts), "the id footer rides the card"
        assert f"games:map:add:{entry.id}" in buttons, "the add-to-pool action rides the row"
        assert f"games:map:edit:{entry.id}" in buttons, "the edit action rides the row"
        assert f"games:map:delete:{entry.id}:aoe2:1" in buttons, "the delete action rides the row"
    finally:
        _restore_caches(saved)
        await server.stop(0)
        os.environ.pop("GAME_AOE2_URI", None)
