"""Unit tests for the pinned admin menu (admin_panel_pin).

The admin channel hosts a permanent pinned menu \u2014 the fully dynamic pin
surface (``admin_panel_dynamic``), guarded at click time. These tests
prove the ensure contract: creation when absent, silence when current,
**replacement of the legacy pins** (the ``admin:select:*`` namespace sent
with live closures), and the click-time denial for a non-admin (seeing
the channel never grants the rights).
"""

from __future__ import annotations

from typing import Any

import discord
import pytest

from kingdoms.core.services.i18n import MessageCatalog
from kingdoms.discord.admin_panel_dynamic import PIN_LOCALE_SELECT_ID, build_pin_main_menu
from kingdoms.discord.admin_panel_pin import ensure_pinned_admin_menu
from tests.mocks.discord_mock import MockGuild, MockInteraction, MockUser


class _FakeChannel(discord.TextChannel):
    """A TextChannel stand-in overriding the discord.py surface used here."""

    def __init__(self, channel_id: str) -> None:
        self.id = int(channel_id)
        self.sent: list[object] = []
        self.pins_list: list[Any] = []

    async def pins(self) -> list[Any]:
        return self.pins_list

    async def send(self, *, view: Any = None, content: str = "") -> Any:
        message = _FakeMessage(view=view, channel=self)
        self.sent.append(message)
        return message

    async def fetch_message(self, message_id: int) -> Any:
        for message in self.sent:
            if message.id == message_id:
                return message
        raise LookupError(f"message {message_id} not found")


class _FakeMessage:
    def __init__(self, view: Any = None, channel: _FakeChannel | None = None) -> None:
        self.view = view
        self.channel = channel
        self.pinned = False
        self.unpinned = False
        self.id = 9001
        self.components: list[Any] = []

    async def pin(self, reason: str = "") -> None:
        self.pinned = True
        if self.channel is not None:
            self.channel.pins_list.append(self)

    async def unpin(self, reason: str = "") -> None:
        self.unpinned = True
        if self.channel is not None and self in self.channel.pins_list:
            self.channel.pins_list.remove(self)


class _FakeAdminChannelService:
    """AdminChannelService stand-in: resolve + deliver (real send)."""

    def __init__(self, channel: _FakeChannel) -> None:
        self.channel = channel

    async def resolve_channel(self, guild_id: str, admin_ids: tuple[str, ...] = ()) -> str | None:
        return str(self.channel.id)

    async def deliver(self, guild_id: str, layout: Any, admin_ids: tuple[str, ...] = ()) -> str | None:
        message = await self.channel.send(view=layout)
        return str(message.id)


class _FakeBot:
    """Client stand-in resolving the guild/channel."""

    def __init__(self, channel: _FakeChannel) -> None:
        self._channel = channel

    def get_guild(self, guild_id: int) -> Any:
        return type("G", (), {"get_channel": lambda _, cid: self._channel, "id": guild_id})()


class _FakeLogsService:
    """LogService stand-in for the menu builder."""

    async def get_locale(self, guild_id: str) -> str:
        return "en"

    async def get_access_policy(self, guild_id: str) -> dict[str, object]:
        return {"default": "admin_only", "roles_with_view": []}

    async def resolve_channel(self, guild_id: str) -> str | None:
        return "555"


def _pin_carrying(components: list[Any]) -> _FakeMessage:
    message = _FakeMessage()
    message.components = components
    return message


@pytest.mark.asyncio
async def test_ensure_creates_and_pins_when_absent() -> None:
    """A guild without a pinned menu gets one: delivered + pinned."""
    channel = _FakeChannel("555")
    bot = _FakeBot(channel)
    service = _FakeAdminChannelService(channel)
    created = await ensure_pinned_admin_menu(
        bot,  # type: ignore[arg-type]
        "42",
        _FakeLogsService(),  # type: ignore[arg-type]
        None,
        service,  # type: ignore[arg-type]
        ("111111111",),
        None,
    )
    assert created is True
    assert channel.sent, "the menu was delivered"
    assert channel.pins_list, "the menu was pinned"
    ids = _component_ids(channel.pins_list[0])
    assert any(cid.startswith("admin:pin:") for cid in ids), "the pinned menu is the pin surface"


@pytest.mark.asyncio
async def test_ensure_stays_silent_when_current() -> None:
    """An existing **current** pinned menu (admin:pin:) is left alone."""
    channel = _FakeChannel("555")
    existing = _pin_carrying([type("C", (), {"custom_id": PIN_LOCALE_SELECT_ID, "children": []})()])
    channel.pins_list = [existing]
    bot = _FakeBot(channel)
    service = _FakeAdminChannelService(channel)
    created = await ensure_pinned_admin_menu(
        bot,  # type: ignore[arg-type]
        "42",
        _FakeLogsService(),  # type: ignore[arg-type]
        None,
        service,  # type: ignore[arg-type]
        ("111111111",),
        None,
    )
    assert created is False
    assert channel.sent == [], "no duplicate menu"


@pytest.mark.asyncio
async def test_ensure_replaces_a_legacy_pinned_menu() -> None:
    """A legacy pin (admin:select:*, sent with live closures) is stale:
    it is replaced by the pin surface and unpinned."""
    channel = _FakeChannel("555")
    legacy = _pin_carrying([type("C", (), {"custom_id": "admin:select:channel", "children": []})()])
    channel.pins_list = [legacy]
    bot = _FakeBot(channel)
    service = _FakeAdminChannelService(channel)
    created = await ensure_pinned_admin_menu(
        bot,  # type: ignore[arg-type]
        "42",
        _FakeLogsService(),  # type: ignore[arg-type]
        None,
        service,  # type: ignore[arg-type]
        ("111111111",),
        None,
    )
    assert created is True, "the legacy pin forces a rebuild"
    assert channel.sent, "the current pin surface was delivered"
    assert legacy.unpinned is True, "the legacy pin is unpinned"
    ids = _component_ids(channel.pins_list[-1])
    assert any(cid.startswith("admin:pin:") for cid in ids), "the new pin is the pin surface"


@pytest.mark.asyncio
async def test_ensure_without_admin_service_degrades() -> None:
    """Without an AdminChannelService there is no pinned surface."""
    created = await ensure_pinned_admin_menu(
        _FakeBot(_FakeChannel("555")),  # type: ignore[arg-type]
        "42",
        _FakeLogsService(),  # type: ignore[arg-type]
        None,
        None,
        ("111111111",),
        None,
    )
    assert created is False


@pytest.mark.asyncio
async def test_the_pinned_menu_carries_no_live_closures() -> None:
    """The <@system> bug: the pinned menu must be fully dynamic \u2014 no
    captured ``by`` can ever answer a click."""
    logs = _FakeLogsService()
    view = await build_pin_main_menu(logs, "42")
    interactive = [c for c in view.walk_children() if getattr(c, "custom_id", None)]
    assert interactive, "the pinned menu carries interactive components"
    assert all(isinstance(c, discord.ui.DynamicItem) for c in interactive)


@pytest.mark.asyncio
async def test_pinned_menu_denies_non_admin_at_click_time() -> None:
    """The pinned menu reuses the /admin guards: a stranger is denied."""
    logs = _FakeLogsService()
    view = await build_pin_main_menu(logs, "42")
    select = None
    for child in view.walk_children():
        custom_id = getattr(child, "custom_id", None)
        if custom_id and str(custom_id).startswith("admin:pin:"):
            select = child
            break
    assert select is not None, "the pinned menu carries interactive components"
    stranger = MockUser(id=999999999)
    interaction = MockInteraction(user=stranger, guild=MockGuild(id=42))
    interaction.guild_id = 42
    interaction.data = {"values": ["fr"]}
    interaction.custom_id = getattr(select, "custom_id", "")
    assert MessageCatalog is not None
    from kingdoms.discord import admin_persistent

    admin_persistent._WIRING_RESOLVER = None
    # a wiring-less bot still denies: no services, no mutation
    await select.callback(interaction)  # type: ignore[union-attr]
    assert interaction.response.sent is True


def _component_ids(message: Any) -> list[str]:
    ids: list[str] = []

    def _walk(component: Any) -> None:
        custom_id = getattr(component, "custom_id", None)
        if isinstance(custom_id, str):
            ids.append(custom_id)
        for attr in ("children", "components"):
            children = getattr(component, attr, None)
            if children and isinstance(children, (list, tuple)):
                for child in children:
                    _walk(child)

    for component in getattr(message, "components", []):
        _walk(component)
    view = getattr(message, "view", None)
    if view is not None:
        for child in view.walk_children():
            custom_id = getattr(child, "custom_id", None)
            if isinstance(custom_id, str):
                ids.append(custom_id)
    return ids


@pytest.mark.asyncio
async def test_ensure_rebuilds_a_pin_predating_the_mods_select() -> None:
    """A current-namespace pin without the mod-sections select is stale:
    when mod sections are registered the pinned panel must carry their
    route select (``admin:pin:mod:mods``) \u2014 the old pin is rebuilt."""
    from kingdoms.discord.admin_panel_mods import (
        AdminModSection,
        mod_section_route_id,
        register_admin_mod_section,
        unregister_admin_mod_section,
    )

    async def _entry(interaction: Any) -> discord.ui.LayoutView:
        return discord.ui.LayoutView(timeout=None)

    register_admin_mod_section(AdminModSection(mod="testmod", label="Test", entry=_entry))
    try:
        channel = _FakeChannel("555")
        old_pin = _pin_carrying([type("C", (), {"custom_id": PIN_LOCALE_SELECT_ID, "children": []})()])
        old_pin.id = 8001
        channel.pins_list = [old_pin]
        bot = _FakeBot(channel)
        service = _FakeAdminChannelService(channel)
        created = await ensure_pinned_admin_menu(
            bot,  # type: ignore[arg-type]
            "42",
            _FakeLogsService(),  # type: ignore[arg-type]
            None,
            service,  # type: ignore[arg-type]
            ("111111111",),
            None,
        )
        assert created is True, "the pre-mods pin forces a rebuild"
        assert old_pin.unpinned is True, "the pre-mods pin is unpinned"
        ids = _component_ids(channel.pins_list[-1])
        assert mod_section_route_id("mods") in ids, "the new pin carries the mods select"
    finally:
        unregister_admin_mod_section("testmod")


@pytest.mark.asyncio
async def test_ensure_keeps_a_pin_carrying_the_mods_select() -> None:
    """A pin already carrying the mod-sections select is current: no-op."""
    from kingdoms.discord.admin_panel_mods import (
        AdminModSection,
        mod_section_route_id,
        register_admin_mod_section,
        unregister_admin_mod_section,
    )

    async def _entry(interaction: Any) -> discord.ui.LayoutView:
        return discord.ui.LayoutView(timeout=None)

    register_admin_mod_section(AdminModSection(mod="testmod", label="Test", entry=_entry))
    try:
        channel = _FakeChannel("555")
        current = _pin_carrying(
            [
                type("C", (), {"custom_id": PIN_LOCALE_SELECT_ID, "children": []})(),
                type("C", (), {"custom_id": mod_section_route_id("mods"), "children": []})(),
            ]
        )
        channel.pins_list = [current]
        bot = _FakeBot(channel)
        service = _FakeAdminChannelService(channel)
        created = await ensure_pinned_admin_menu(
            bot,  # type: ignore[arg-type]
            "42",
            _FakeLogsService(),  # type: ignore[arg-type]
            None,
            service,  # type: ignore[arg-type]
            ("111111111",),
            None,
        )
        assert created is False
        assert channel.sent == [], "no rebuild"
        assert current.unpinned is False
    finally:
        unregister_admin_mod_section("testmod")
