"""Unit tests for the persistent admin panel (restart-proof, #122).

The §3b contract applied to the admin panel: a restart wipes every
view instance — the pinned admin menu must still answer. These tests
pin the custom_id reconstruction (same ids as the live panel), the
click-time guard, the wiring degradation and the post-restart
re-render through the real services.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from kingdoms.discord.admin import (
    BACK_BUTTON_ID,
    CHANNEL_MENU_ID,
    CHANNEL_ROUTE_ID,
    LOCALE_SELECT_ID,
    USER_LOCALE_SELECT_ID,
    VISIBILITY_SELECT_ID,
)
from kingdoms.discord.admin_persistent import (
    AdminDynamicButton,
    AdminDynamicRoute,
    AdminDynamicSelect,
    register_admin_panel_bot,
    register_admin_persistent_items,
)
from tests.mocks.discord_mock import MockGuild, MockInteraction, MockUser

SELECT_TEMPLATE = r"admin:select:(?P<select>[a-z0-9-]+)"
ROUTE_TEMPLATE = r"admin:channels:(?P<channel>[a-z0-9_]+)"
BUTTON_TEMPLATE = r"admin:button:(?P<button>[a-z0-9-]+)"


class _FakeLogsService:
    """LogService stand-in recording every admin mutation."""

    def __init__(self) -> None:
        self.locales_set: list[tuple[str, str, str]] = []
        self.user_locales_set: list[tuple[str, str]] = []
        self.visibilities: list[tuple[str, bool, str]] = []
        self.routed: list[tuple[str, str, str]] = []

    async def get_locale(self, guild_id: str) -> str:
        return "en"

    async def set_locale(self, guild_id: str, locale: str, by: str) -> None:
        self.locales_set.append((guild_id, locale, by))

    async def get_user_locale(self, user_id: str) -> str:
        return "en"

    async def set_user_locale(self, user_id: str, locale: str) -> None:
        self.user_locales_set.append((user_id, locale))

    async def set_visibility(self, guild_id: str, public: bool, by: str) -> None:
        self.visibilities.append((guild_id, public, by))

    async def set_channel(self, guild_id: str, channel_id: str, by: str) -> None:
        self.routed.append((guild_id, channel_id, by))

    async def get_access_policy(self, guild_id: str) -> dict[str, object]:
        return {"default": "admin_only", "roles_with_view": []}

    async def resolve_channel(self, guild_id: str) -> str | None:
        return "555"


class _FakeBot:
    """Bot stand-in: the wiring resolves the services at click time."""

    status_service = type("S", (), {"bot_admins": ("111111111",)})()
    messages = None
    roles_service = None
    admin_channel_service = None
    crash_report = None

    def __init__(self, logs: _FakeLogsService) -> None:
        self.logs_service = logs


def _wire(logs: _FakeLogsService) -> None:
    register_admin_panel_bot(_FakeBot(logs))


def _interaction(user_id: int = 111111111, guild_id: int = 42) -> MockInteraction:
    interaction = MockInteraction(user=MockUser(id=user_id), guild=MockGuild(id=guild_id))
    interaction.guild_id = guild_id
    return interaction


def _select(select: str) -> AdminDynamicSelect:
    match = re.fullmatch(SELECT_TEMPLATE, f"admin:select:{select}")
    assert match is not None
    return AdminDynamicSelect(select)


class TestTemplateResolution:
    def test_the_templates_match_the_panel_custom_ids(self) -> None:
        """The DynamicItems serve the exact ids the live panel sends."""
        assert re.fullmatch(SELECT_TEMPLATE, LOCALE_SELECT_ID)
        assert re.fullmatch(SELECT_TEMPLATE, USER_LOCALE_SELECT_ID)
        assert re.fullmatch(SELECT_TEMPLATE, CHANNEL_MENU_ID)
        assert re.fullmatch(SELECT_TEMPLATE, VISIBILITY_SELECT_ID)
        assert re.fullmatch(ROUTE_TEMPLATE, CHANNEL_ROUTE_ID)
        assert re.fullmatch(BUTTON_TEMPLATE, BACK_BUTTON_ID)

    def test_foreign_ids_are_rejected(self) -> None:
        assert re.fullmatch(SELECT_TEMPLATE, "clans:select:locale") is None
        assert re.fullmatch(ROUTE_TEMPLATE, LOCALE_SELECT_ID) is None


class TestStateReconstruction:
    @pytest.mark.asyncio
    async def test_every_item_rebuilds_from_its_custom_id(self) -> None:
        select = await AdminDynamicSelect.from_custom_id(None, None, re.fullmatch(SELECT_TEMPLATE, LOCALE_SELECT_ID))  # type: ignore[arg-type]
        assert select.select == "locale"
        route = await AdminDynamicRoute.from_custom_id(None, None, re.fullmatch(ROUTE_TEMPLATE, CHANNEL_ROUTE_ID))  # type: ignore[arg-type]
        assert route.channel == "logs"
        button = await AdminDynamicButton.from_custom_id(None, None, re.fullmatch(BUTTON_TEMPLATE, BACK_BUTTON_ID))  # type: ignore[arg-type]
        assert button.button == "back"


class TestPostRestartClicks:
    @pytest.mark.asyncio
    async def test_locale_select_click_persists_and_rerenders(self) -> None:
        """A post-restart locale click flows through the services."""
        logs = _FakeLogsService()
        _wire(logs)
        item = _select("locale")
        interaction = _interaction()
        interaction.data = {"values": ["fr"]}
        await item.callback(interaction)
        assert logs.locales_set == [("42", "fr", "111111111")]
        message = interaction.response.message
        assert message is not None and message.layout is not None, "the panel re-renders in place"
        assert LOCALE_SELECT_ID in _custom_ids(message.layout), "the re-rendered panel is the main menu"

    @pytest.mark.asyncio
    async def test_channel_menu_click_opens_the_secondary_menu(self) -> None:
        logs = _FakeLogsService()
        _wire(logs)
        item = _select("channel")
        interaction = _interaction()
        interaction.data = {"values": ["bot_logs"]}
        await item.callback(interaction)
        message = interaction.response.message
        assert message is not None and message.layout is not None
        assert BACK_BUTTON_ID in _custom_ids(message.layout), "the channel menu renders with its back button"

    @pytest.mark.asyncio
    async def test_visibility_click_persists(self) -> None:
        logs = _FakeLogsService()
        _wire(logs)
        item = _select("visibility")
        interaction = _interaction()
        interaction.data = {"values": ["public"]}
        await item.callback(interaction)
        assert logs.visibilities == [("42", True, "111111111")]
        assert interaction.response.message is not None, "the menu re-renders after the change"

    @pytest.mark.asyncio
    async def test_user_locale_click_persists(self) -> None:
        logs = _FakeLogsService()
        _wire(logs)
        item = _select("user-locale")
        interaction = _interaction()
        interaction.data = {"values": ["fr"]}
        await item.callback(interaction)
        assert logs.user_locales_set == [("111111111", "fr")]

    @pytest.mark.asyncio
    async def test_route_click_persists_the_channel(self) -> None:
        logs = _FakeLogsService()
        _wire(logs)
        item = await AdminDynamicRoute.from_custom_id(None, None, re.fullmatch(ROUTE_TEMPLATE, CHANNEL_ROUTE_ID))  # type: ignore[arg-type]
        interaction = _interaction()
        interaction.data = {"values": ["999"]}
        await item.callback(interaction)
        assert logs.routed == [("42", "999", "111111111")]
        assert interaction.response.message is not None, "the menu re-renders after the routing"

    @pytest.mark.asyncio
    async def test_back_button_returns_to_the_main_menu(self) -> None:
        logs = _FakeLogsService()
        _wire(logs)
        item = await AdminDynamicButton.from_custom_id(None, None, re.fullmatch(BUTTON_TEMPLATE, BACK_BUTTON_ID))  # type: ignore[arg-type]
        interaction = _interaction()
        await item.callback(interaction)
        message = interaction.response.message
        assert message is not None and message.layout is not None
        assert CHANNEL_MENU_ID in _custom_ids(message.layout), "back lands on the main menu"


class TestClickTimeGuards:
    @pytest.mark.asyncio
    async def test_a_stranger_click_is_denied_at_click_time(self) -> None:
        """The developer mandate: seeing the pinned menu grants nothing."""
        logs = _FakeLogsService()
        _wire(logs)
        item = _select("locale")
        interaction = _interaction(user_id=999999999)
        interaction.data = {"values": ["fr"]}
        await item.callback(interaction)
        assert logs.locales_set == [], "no mutation for a stranger"
        assert interaction.response.sent is True, "the stranger gets the denial answer"

    @pytest.mark.asyncio
    async def test_without_wiring_the_click_degrades(self) -> None:
        from kingdoms.discord import admin_persistent

        admin_persistent._WIRING_RESOLVER = None
        item = _select("locale")
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
        assert set(bot.registered) == {AdminDynamicSelect, AdminDynamicRoute, AdminDynamicButton}


def _custom_ids(view: Any) -> set[str]:
    """Collect the custom ids of a rendered layout."""
    ids: set[str] = set()
    for child in view.walk_children():
        custom_id = getattr(child, "custom_id", None)
        if isinstance(custom_id, str):
            ids.add(custom_id)
    return ids
