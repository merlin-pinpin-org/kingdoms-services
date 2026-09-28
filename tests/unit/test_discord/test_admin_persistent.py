"""Unit tests for the persistent admin panel (restart-proof, #122).

The §3b contract applied to the pinned admin menu: a restart wipes every
view instance \u2014 the pinned menu must still answer. These tests pin the
``admin:pin:`` namespace (one custom_id, one dispatch mechanism), the
click-time guard, the wiring degradation, the category-carrying routing
payload, and the post-restart re-render through the real services.
"""

from __future__ import annotations

import re
from typing import Any

import discord
import pytest

from kingdoms.discord.admin_panel_dynamic import (
    PIN_BACK_BUTTON_ID,
    PIN_CHANNEL_MENU_ID,
    PIN_LOCALE_SELECT_ID,
    PIN_VISIBILITY_SELECT_ID,
    PinBackButton,
    PinChannelMenu,
    PinLocaleSelect,
    PinRouteSelect,
    PinVisibilitySelect,
    build_pin_channel_menu,
    build_pin_main_menu,
    pin_route_id,
)
from kingdoms.discord.admin_persistent import (
    register_admin_panel_bot,
    register_admin_persistent_items,
)
from tests.mocks.discord_mock import MockGuild, MockInteraction, MockUser

PIN_SELECT_TEMPLATE = r"admin:pin:select:(?P<select>[a-z-]+)"
PIN_ROUTE_TEMPLATE = r"admin:pin:route:(?P<category>[a-z_]+)"
PIN_BUTTON_TEMPLATE = r"admin:pin:button:(?P<button>[a-z-]+)"


class _FakeLogsService:
    """LogService stand-in recording every admin mutation."""

    def __init__(self) -> None:
        self.locales_set: list[tuple[str, str, str]] = []
        self.visibilities: list[tuple[str, bool, str]] = []
        self.routed: list[tuple[str, str, str]] = []

    async def get_locale(self, guild_id: str) -> str:
        return "en"

    async def set_locale(self, guild_id: str, locale: str, by: str) -> None:
        self.locales_set.append((guild_id, locale, by))

    async def set_visibility(self, guild_id: str, public: bool, by: str) -> None:
        self.visibilities.append((guild_id, public, by))

    async def set_channel(self, guild_id: str, channel_id: str, by: str) -> None:
        self.routed.append((guild_id, channel_id, by))

    async def get_access_policy(self, guild_id: str) -> dict[str, object]:
        return {"default": "admin_only", "roles_with_view": []}

    async def resolve_channel(self, guild_id: str) -> str | None:
        return "555"


class _FakeAdminChannelService:
    """AdminChannelService stand-in recording the routing."""

    def __init__(self) -> None:
        self.routed: list[tuple[str, str]] = []

    async def resolve_channel(self, guild_id: str, admin_ids: tuple[str, ...] = ()) -> str | None:
        return "666"

    async def set_channel(self, guild_id: str, channel_id: str) -> None:
        self.routed.append((guild_id, channel_id))


class _FakeBot:
    """Bot stand-in: the wiring resolves the services at click time."""

    status_service = type("S", (), {"bot_admins": ("111111111",)})()
    messages = None
    roles_service = None
    crash_report = None

    def __init__(self, logs: _FakeLogsService, admin_channel: Any = None) -> None:
        self.logs_service = logs
        self.admin_channel_service = admin_channel


def _wire(logs: _FakeLogsService, admin_channel: Any = None) -> None:
    register_admin_panel_bot(_FakeBot(logs, admin_channel))


def _interaction(user_id: int = 111111111, guild_id: int = 42) -> MockInteraction:
    interaction = MockInteraction(user=MockUser(id=user_id), guild=MockGuild(id=guild_id))
    interaction.guild_id = guild_id
    return interaction


def _custom_ids(view: Any) -> set[str]:
    """Collect the custom ids of a rendered layout."""
    ids: set[str] = set()
    for child in view.walk_children():
        custom_id = getattr(child, "custom_id", None)
        if isinstance(custom_id, str):
            ids.add(custom_id)
    return ids


class TestNamespace:
    def test_the_pin_ids_live_in_their_own_namespace(self) -> None:
        """One custom_id, one dispatch mechanism: the pin ids never collide
        with the live /admin panel ids (the <@system> double-dispatch bug)."""
        live_ids = {
            "admin:select:locale",
            "admin:select:channel",
            "admin:select:user-locale",
            "admin:select:visibility",
            "admin:channels:logs",
            "admin:button:back",
        }
        pin_ids = {
            PIN_LOCALE_SELECT_ID,
            PIN_CHANNEL_MENU_ID,
            PIN_VISIBILITY_SELECT_ID,
            PIN_BACK_BUTTON_ID,
            pin_route_id("bot_logs"),
            pin_route_id("bot_admins"),
        }
        assert all(cid.startswith("admin:pin:") for cid in pin_ids)
        assert not (live_ids & pin_ids), "no id serves both a live closure and a dynamic handler"

    def test_the_templates_match_the_pin_ids(self) -> None:
        assert re.fullmatch(PIN_SELECT_TEMPLATE, PIN_LOCALE_SELECT_ID)
        assert re.fullmatch(PIN_SELECT_TEMPLATE, PIN_CHANNEL_MENU_ID)
        assert re.fullmatch(PIN_SELECT_TEMPLATE, PIN_VISIBILITY_SELECT_ID)
        assert re.fullmatch(PIN_ROUTE_TEMPLATE, pin_route_id("bot_logs"))
        assert re.fullmatch(PIN_ROUTE_TEMPLATE, pin_route_id("bot_admins"))
        assert re.fullmatch(PIN_BUTTON_TEMPLATE, PIN_BACK_BUTTON_ID)

    def test_no_pin_template_matches_the_live_panel_ids(self) -> None:
        """The /admin ephemeral ids belong to their live closures only."""
        assert discord.ui.DynamicItem.from_custom_id is not None
        from kingdoms.discord.ui.persistent import PersistentPagerButton

        templates = [
            PinLocaleSelect.__discord_ui_compiled_template__,
            PinChannelMenu.__discord_ui_compiled_template__,
            PinVisibilitySelect.__discord_ui_compiled_template__,
            PinRouteSelect.__discord_ui_compiled_template__,
            PinBackButton.__discord_ui_compiled_template__,
            PersistentPagerButton.__discord_ui_compiled_template__,
        ]
        for live_id in ("admin:select:locale", "admin:select:channel", "admin:channels:logs", "admin:button:back"):
            for pattern in templates:
                assert pattern.fullmatch(live_id) is None, f"{pattern.pattern} must not match {live_id}"

    def test_the_routing_payload_carries_the_category(self) -> None:
        """The legacy id was shared by both menus (always routed to logs)."""
        assert pin_route_id("bot_logs") != pin_route_id("bot_admins")
        assert pin_route_id("bot_admins") == "admin:pin:route:bot_admins"


class TestStateReconstruction:
    @pytest.mark.asyncio
    async def test_every_item_rebuilds_from_its_custom_id(self) -> None:
        select = await PinLocaleSelect.from_custom_id(
            None, None, re.fullmatch(PIN_SELECT_TEMPLATE, PIN_LOCALE_SELECT_ID)  # type: ignore[arg-type]
        )
        assert isinstance(select, PinLocaleSelect)
        route = await PinRouteSelect.from_custom_id(
            None, None, re.fullmatch(PIN_ROUTE_TEMPLATE, pin_route_id("bot_admins"))  # type: ignore[arg-type]
        )
        assert route.category == "bot_admins"
        button = await PinBackButton.from_custom_id(
            None, None, re.fullmatch(PIN_BUTTON_TEMPLATE, PIN_BACK_BUTTON_ID)  # type: ignore[arg-type]
        )
        assert isinstance(button, PinBackButton)


class TestFullyDynamicSurface:
    @pytest.mark.asyncio
    async def test_the_pinned_main_menu_is_fully_dynamic(self) -> None:
        """A view whose children are all dynamic items is never registered
        as a live view: exactly one dispatch path per click."""
        logs = _FakeLogsService()
        view = await build_pin_main_menu(logs, "42")
        children = list(view.walk_children())
        interactive = [c for c in children if getattr(c, "custom_id", None)]
        assert interactive, "the menu carries interactive components"
        assert all(isinstance(c, discord.ui.DynamicItem) for c in interactive), (
            "every interactive child must be a DynamicItem (no live closures)"
        )
        from discord.ui.view import ViewStore

        store = ViewStore(None)
        store.add_view(view, 12345)
        assert 12345 not in store._views, "a fully dynamic view must not be registered as a live view"

    @pytest.mark.asyncio
    async def test_the_pinned_channel_menu_is_fully_dynamic(self) -> None:
        logs = _FakeLogsService()
        view = await build_pin_channel_menu(logs, "42", "bot_logs")
        interactive = [c for c in view.walk_children() if getattr(c, "custom_id", None)]
        assert interactive
        assert all(isinstance(c, discord.ui.DynamicItem) for c in interactive)
        ids = {c.custom_id for c in interactive}
        assert pin_route_id("bot_logs") in ids
        assert PIN_VISIBILITY_SELECT_ID in ids
        assert PIN_BACK_BUTTON_ID in ids

    @pytest.mark.asyncio
    async def test_the_admin_channel_menu_routes_with_its_own_category(self) -> None:
        logs = _FakeLogsService()
        view = await build_pin_channel_menu(logs, "42", "bot_admins")
        ids = _custom_ids(view)
        assert pin_route_id("bot_admins") in ids, "the admin channel menu routes through its own payload"
        assert PIN_VISIBILITY_SELECT_ID not in ids, "visibility is a logs-channel setting"


class TestPostRestartClicks:
    @pytest.mark.asyncio
    async def test_locale_select_click_persists_as_the_real_user(self) -> None:
        """The <@system> bug: the change must be audited exactly once, by
        the clicking user \u2014 never by a captured ``by="system"``."""
        logs = _FakeLogsService()
        _wire(logs)
        item = PinLocaleSelect()
        interaction = _interaction()
        interaction.data = {"values": ["fr"]}
        await item.callback(interaction)
        assert logs.locales_set == [("42", "fr", "111111111")]
        message = interaction.response.message
        assert message is not None and message.layout is not None, "the panel re-renders in place"
        assert PIN_LOCALE_SELECT_ID in _custom_ids(message.layout), "the re-rendered panel is the pin main menu"

    @pytest.mark.asyncio
    async def test_channel_menu_click_opens_the_secondary_menu(self) -> None:
        logs = _FakeLogsService()
        _wire(logs)
        item = PinChannelMenu([])
        interaction = _interaction()
        interaction.data = {"values": ["bot_logs"]}
        await item.callback(interaction)
        message = interaction.response.message
        assert message is not None and message.layout is not None
        assert PIN_BACK_BUTTON_ID in _custom_ids(message.layout), "the channel menu renders with its back button"

    @pytest.mark.asyncio
    async def test_visibility_click_persists(self) -> None:
        logs = _FakeLogsService()
        _wire(logs)
        item = PinVisibilitySelect([])
        interaction = _interaction()
        interaction.data = {"values": ["public"]}
        await item.callback(interaction)
        assert logs.visibilities == [("42", True, "111111111")]
        assert interaction.response.message is not None, "the menu re-renders after the change"

    @pytest.mark.asyncio
    async def test_route_click_persists_the_logs_channel(self) -> None:
        logs = _FakeLogsService()
        _wire(logs)
        item = PinRouteSelect("bot_logs")
        interaction = _interaction()
        interaction.data = {"values": ["999"]}
        await item.callback(interaction)
        assert logs.routed == [("42", "999", "111111111")]

    @pytest.mark.asyncio
    async def test_route_click_persists_the_admin_channel_through_its_category(self) -> None:
        """The legacy shared id always routed to the logs channel."""
        logs = _FakeLogsService()
        admin_channel = _FakeAdminChannelService()
        _wire(logs, admin_channel)
        item = PinRouteSelect("bot_admins")
        interaction = _interaction()
        interaction.data = {"values": ["888"]}
        await item.callback(interaction)
        assert logs.routed == [], "the logs channel must not be touched"
        assert admin_channel.routed == [("42", "888")]

    @pytest.mark.asyncio
    async def test_back_button_returns_to_the_main_menu(self) -> None:
        logs = _FakeLogsService()
        _wire(logs)
        item = PinBackButton()
        interaction = _interaction()
        await item.callback(interaction)
        message = interaction.response.message
        assert message is not None and message.layout is not None
        assert PIN_CHANNEL_MENU_ID in _custom_ids(message.layout), "back lands on the main menu"


class TestClickTimeGuards:
    @pytest.mark.asyncio
    async def test_a_stranger_click_is_denied_at_click_time(self) -> None:
        """The developer mandate: seeing the pinned menu grants nothing."""
        logs = _FakeLogsService()
        _wire(logs)
        item = PinLocaleSelect()
        interaction = _interaction(user_id=999999999)
        interaction.data = {"values": ["fr"]}
        await item.callback(interaction)
        assert logs.locales_set == [], "no mutation for a stranger"
        assert interaction.response.deferred is True, "the click acknowledges within the 3s window"
        assert interaction.followup.messages, "the stranger gets the denial answer"

    @pytest.mark.asyncio
    async def test_without_wiring_the_click_degrades(self) -> None:
        from kingdoms.discord import admin_persistent

        admin_persistent._WIRING_RESOLVER = None
        item = PinLocaleSelect()
        interaction = _interaction()
        interaction.data = {"values": ["fr"]}
        await item.callback(interaction)
        assert interaction.response.sent is True, "the degradation note answers the click"

    @pytest.mark.asyncio
    async def test_the_wiring_sees_the_services_built_after_startup(self) -> None:
        """The ordering bug: a wiring snapshot taken in setup_hook saw
        services=None (the factory builds them later). The bot-based
        resolver must see them at click time."""
        from kingdoms.discord import admin_persistent

        bot = _FakeBot(_FakeLogsService())
        register_admin_panel_bot(bot)
        bot.admin_channel_service = object()  # built later by the factory
        wiring = admin_persistent._wiring()
        assert wiring is not None
        assert wiring.admin_channel_service is bot.admin_channel_service


class TestStartupRegistration:
    def test_register_admin_persistent_items_wires_the_classes(self) -> None:
        class FakeBot:
            def __init__(self) -> None:
                self.registered: list[Any] = []

            def add_dynamic_items(self, *items: Any) -> None:
                self.registered.extend(items)

        bot = FakeBot()
        register_admin_persistent_items(bot)
        assert set(bot.registered) == {
            PinLocaleSelect,
            PinChannelMenu,
            PinVisibilitySelect,
            PinRouteSelect,
            PinBackButton,
        }
