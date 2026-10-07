"""Tests for the pinned-menu service: one self-healing pinned menu per surface.

The lifecycle contract: a menu is identified by a marker custom-id
namespace, ``ensure`` is idempotent (a current pin is a no-op), a
stale menu of the same namespace is unpinned after the rebuild, and
everything is best-effort.
"""
from __future__ import annotations

from typing import Any

import pytest

from kingdoms.core.services.pinned_menu import PinnedMenuService


class _FakeMessage:
    """Message stand-in with a components tree and pin tracking."""

    def __init__(self, message_id: str, custom_ids: list[str], channel: _FakeChannel | None = None) -> None:
        self.id = message_id
        self._channel = channel
        self.pinned = False
        self.unpinned = False
        self.components = [_FakeComponent(cid) for cid in custom_ids]

    async def pin(self, reason: str = "") -> None:
        self.pinned = True
        if self._channel is not None and self not in self._channel.pins_list:
            self._channel.pins_list.append(self)

    async def unpin(self, reason: str = "") -> None:
        self.unpinned = True


class _FakeComponent:
    """Component stand-in carrying one custom_id."""

    def __init__(self, custom_id: str) -> None:
        self.custom_id = custom_id
        self.children: list[Any] = []


class _FakeChannel:
    """Channel stand-in: the pins, the sent messages, fetch by id."""

    def __init__(self) -> None:
        self.pins_list: list[_FakeMessage] = []
        self.sent: list[_FakeMessage] = []

    async def pins(self) -> list[_FakeMessage]:
        return self.pins_list

    async def send(self, layout: Any) -> _FakeMessage:
        message = _FakeMessage(str(9000 + len(self.sent)), ["home:pin:menu"], channel=self)
        self.sent.append(message)
        return message

    async def fetch_message(self, message_id: int) -> _FakeMessage:
        for message in self.sent:
            if message.id == str(message_id):
                return message
        raise LookupError(str(message_id))


class _FakeDelivery:
    """Delivery stand-in sending through the fake channel."""

    def __init__(self, channel: _FakeChannel) -> None:
        self._channel = channel

    async def deliver(self, channel: Any, layout: Any) -> str:
        message = await self._channel.send(layout)
        return message.id


async def _ensure(
    channel: _FakeChannel,
    marker: str = "home:pin:",
    required_ids: tuple[str, ...] = (),
) -> bool:
    async def _build(guild_id: str) -> Any:
        return object()

    return await PinnedMenuService(_FakeDelivery(channel)).ensure(
        "42",
        channel,  # type: ignore[arg-type]
        marker=marker,
        build_layout=_build,
        pin_reason="test",
        required_ids=required_ids,
    )


@pytest.mark.asyncio
async def test_ensure_creates_and_pins_when_absent() -> None:
    """An empty channel gets the menu delivered and pinned."""
    channel = _FakeChannel()
    assert await _ensure(channel) is True
    assert channel.sent[0].pinned is True


@pytest.mark.asyncio
async def test_ensure_is_a_noop_when_current() -> None:
    """A pinned message carrying the marker namespace is already current."""
    channel = _FakeChannel()
    assert await _ensure(channel) is True
    assert await _ensure(channel) is False
    assert len(channel.sent) == 1


@pytest.mark.asyncio
async def test_ensure_treats_any_marker_pin_as_current() -> None:
    """A pin carrying the marker namespace is current: no rebuild, no unpin."""
    channel = _FakeChannel()
    existing = _FakeMessage("1", ["home:pin:menu"], channel=channel)
    channel.pins_list = [existing]
    assert await _ensure(channel) is False
    assert existing.unpinned is False
    assert channel.sent == []


@pytest.mark.asyncio
async def test_ensure_leaves_foreign_pins_alone() -> None:
    """A pinned message of another namespace is never touched."""
    channel = _FakeChannel()
    foreign = _FakeMessage("1", ["admin:pin:menu"])
    channel.pins_list = [foreign]
    assert await _ensure(channel) is True
    assert foreign.unpinned is False


@pytest.mark.asyncio
async def test_required_id_missing_forces_rebuild() -> None:
    """A pin lacking the current-revision id is stale: rebuilt and unpinned."""
    channel = _FakeChannel()
    existing = _FakeMessage("1", ["home:pin:menu"], channel=channel)
    channel.pins_list = [existing]
    assert await _ensure(channel, required_ids=("home:pin:menu:rev2",)) is True
    assert existing.unpinned is True
    assert channel.sent, "the menu was rebuilt"


@pytest.mark.asyncio
async def test_required_id_present_is_a_noop() -> None:
    """A pin carrying every required id is current: no rebuild."""
    channel = _FakeChannel()
    existing = _FakeMessage("1", ["home:pin:menu", "home:pin:menu:rev2"], channel=channel)
    channel.pins_list = [existing]
    assert await _ensure(channel, required_ids=("home:pin:menu:rev2",)) is False
    assert existing.unpinned is False
    assert channel.sent == []
