"""Unit tests for persistent components (#122) — state reconstruction.

The §3b contract: a restart wipes every view instance; the custom_id
alone must rebuild the item and its state. These pin the template
resolution, the payload round-trip and the startup registration.
"""

from __future__ import annotations

import re
from typing import Any

import discord
import pytest

from kingdoms.discord.ui.persistent import (
    PersistentPagerButton,
    register_page_renderer,
    register_persistent_items,
)

TEMPLATE = r"(?P<mod>[a-z0-9_]+):page:(?P<page>\d+)"


class TestTemplateResolution:
    def test_the_template_matches_the_convention(self) -> None:
        match = re.fullmatch(TEMPLATE, "ladder:page:3")
        assert match is not None
        assert match["mod"] == "ladder"
        assert match["page"] == "3"

    def test_the_template_rejects_foreign_ids(self) -> None:
        assert re.fullmatch(TEMPLATE, "clans:join:confirm") is None
        assert re.fullmatch(TEMPLATE, "ladder:page:next") is None

    def test_the_payload_rides_the_custom_id(self) -> None:
        button = PersistentPagerButton("ladder", 17)
        assert button.item.custom_id == "ladder:page:17"
        assert button.mod == "ladder"
        assert button.page == 17

    def test_the_custom_id_stays_under_the_wire_limit(self) -> None:
        button = PersistentPagerButton("a_mod_with_a_long_name", 42)
        assert len(button.item.custom_id) <= 100


class TestStateReconstruction:
    @pytest.mark.asyncio
    async def test_from_custom_id_rebuilds_the_state(self) -> None:
        match = re.fullmatch(TEMPLATE, "ladder:page:5")
        assert match is not None
        rebuilt = await PersistentPagerButton.from_custom_id(None, None, match)  # type: ignore[arg-type]
        assert rebuilt.mod == "ladder"
        assert rebuilt.page == 5
        assert rebuilt.item.custom_id == "ladder:page:5"


class TestPageRenderers:
    def test_a_registered_renderer_receives_the_page(self) -> None:
        seen: list[tuple[Any, int]] = []

        async def renderer(_interaction: Any, page: int) -> None:
            seen.append(("ok", page))

        register_page_renderer("ladder", renderer)
        from kingdoms.discord.ui.persistent import _page_renderers

        assert "ladder" in _page_renderers()


class TestStartupRegistration:
    def test_register_persistent_items_wires_the_classes(self) -> None:
        class FakeBot:
            def __init__(self) -> None:
                self.registered: list[Any] = []

            def add_dynamic_items(self, *items: Any) -> None:
                self.registered.extend(items)

        bot = FakeBot()
        register_persistent_items(bot)  # type: ignore[arg-type]
        assert PersistentPagerButton in bot.registered

    def test_the_button_is_timeout_free_by_construction(self) -> None:
        button = PersistentPagerButton("ladder", 2)
        view = discord.ui.View(timeout=None)
        view.add_item(button.item)
        assert view.timeout is None
