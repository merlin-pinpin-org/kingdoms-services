"""Core pinned-menu service: one self-healing pinned menu per channel surface.

Several bot surfaces live as a **pinned menu** in a dedicated channel (the
admin home, the guild home): the message must survive deletion, unpinning
and channel re-provisioning without an operator typing anything.

This service owns that lifecycle, once, for every surface:

- a menu is identified by a marker custom-id namespace (e.g.
  ``home:pin:``) carried by one of its components;
- ``ensure`` creates the menu, delivers it through a channel-delivery
  seam and pins it — a current menu already in the pins is a no-op;
- stale menus of the same namespace (older surface revisions) are
  unpinned after the rebuild;
- everything is best-effort: a failing pin logs, never raises — the
  periodic re-check (the factory heartbeat) makes the surface
  self-healing.

The surface itself (what the menu shows, its buttons) is *not* this
service's business: the caller provides a layout builder, the seam
delivers it. The admin pin and the guild home are two builders over the
same lifecycle.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

logger = logging.getLogger("kingdoms.core.pinned_menu")


class PinnedMenuChannel(Protocol):
    """Narrow seam: the channel that hosts the pinned menu."""

    @property
    def id(self) -> str:
        """The channel id."""
        ...

    async def pins(self) -> list[Any]:
        """Return the channel's pinned messages."""
        ...

    async def fetch_message(self, message_id: int) -> Any:
        """Fetch one message by id."""
        ...

    async def pin(self, reason: str) -> None:
        """Pin this message (called on the fetched message)."""
        ...

    async def unpin(self, reason: str) -> None:
        """Unpin this message (called on a pinned message)."""
        ...


class PinnedMenuDelivery(Protocol):
    """Narrow seam: deliver a layout into the channel; the message id."""

    async def deliver(self, channel: PinnedMenuChannel, layout: Any) -> str:
        """Send the layout; the created message id."""
        ...


class PinnedMenuUpdater(Protocol):
    """Narrow seam: edit an existing message to a new layout; True on success."""

    async def update(self, channel: PinnedMenuChannel, message_id: str, layout: Any) -> bool:
        """Edit the message's view in place; False when it can't be edited."""
        ...


LayoutBuilder = Callable[[str], Awaitable[Any]]


class PinnedMenuService:
    """Keep one pinned menu alive in one channel (idempotent, best-effort)."""

    def __init__(self, delivery: PinnedMenuDelivery, updater: PinnedMenuUpdater | None = None) -> None:
        self._delivery = delivery
        self._updater = updater

    async def ensure(
        self,
        guild_id: str,
        channel: PinnedMenuChannel,
        marker: str,
        build_layout: LayoutBuilder,
        pin_reason: str,
        required_ids: tuple[str, ...] = (),
    ) -> bool:
        """Ensure the channel holds exactly one current pinned menu.

        Returns True when a message was (re-)created, False when the
        existing pin was already current. A menu is *current* when one of
        its components carries the marker namespace **and every id in
        ``required_ids`` is present** — the caller pins the menu revision
        (e.g. the admin surface's mod-sections select) so a structural
        change propagates: an older pin is rebuilt, then unpinned with
        the other stale menus of the same namespace.
        """
        if await self._current_menu_exists(channel, marker, required_ids):
            return False
        if await self._update_current(channel, marker, required_ids, build_layout, guild_id):
            return False
        layout = await build_layout(guild_id)
        message_id = await self._delivery.deliver(channel, layout)
        if message_id is None:
            logger.warning("PINNED MENU delivery failed (guild %s, marker %s)", guild_id, marker)
            return False
        pinned = await self._pin_message(channel, message_id, pin_reason)
        await self._unpin_stale(channel, marker, keep_message_id=message_id)
        return pinned

    async def _update_current(
        self,
        channel: PinnedMenuChannel,
        marker: str,
        required_ids: tuple[str, ...],
        build_layout: LayoutBuilder,
        guild_id: str,
    ) -> bool:
        """Edit the existing (stale-revision) menu in place instead of re-posting.

        A pinned menu found by marker but missing some ``required_ids`` is
        an older revision of the same surface: it keeps its message id
        (panels stay unique per channel) and only its view is edited to
        the current revision. Falls back to a re-post when the updater
        is absent or the edit fails (message deleted, no permission).
        """
        if self._updater is None:
            return False
        for message in await self._safe_pins(channel):
            if not (self._carries_marker(message, marker) and self._carries_required(message, ())) :
                continue
            if self._carries_required(message, required_ids):
                continue
            layout = await build_layout(guild_id)
            try:
                edited = await self._updater.update(channel, str(getattr(message, "id", "")), layout)
            except Exception:
                logger.warning(
                    "PINNED MENU in-place update failed (message %s)",
                    getattr(message, "id", "?"),
                    exc_info=True,
                )
                return False
            if edited:
                try:
                    await message.pin(reason="kingdoms: pinned menu revision update")
                except Exception:
                    logger.warning("PINNED MENU re-pin after update failed (message %s)", getattr(message, "id", "?"))
                return True
            return False
        return False

    async def _current_menu_exists(
        self,
        channel: PinnedMenuChannel,
        marker: str,
        required_ids: tuple[str, ...] = (),
    ) -> bool:
        for message in await self._safe_pins(channel):
            if self._carries_marker(message, marker) and self._carries_required(message, required_ids):
                return True
        return False

    @staticmethod
    def _carries_required(message: Any, required_ids: tuple[str, ...]) -> bool:
        """Whether the message carries every id of the current revision."""
        if not required_ids:
            return True
        ids = set(_walk_custom_ids(message))
        return all(required in ids for required in required_ids)

    async def _unpin_stale(
        self,
        channel: PinnedMenuChannel,
        marker: str,
        keep_message_id: str,
    ) -> None:
        for message in await self._safe_pins(channel):
            if str(getattr(message, "id", "")) == keep_message_id:
                continue
            if self._carries_marker(message, marker):
                try:
                    await message.unpin(reason="kingdoms: superseded pinned menu")
                except Exception:
                    logger.warning("PINNED MENU unpin failed (message %s)", getattr(message, "id", "?"))

    async def _pin_message(self, channel: PinnedMenuChannel, message_id: str, reason: str) -> bool:
        try:
            message = await channel.fetch_message(int(message_id))
            await message.pin(reason=reason)
            return True
        except Exception:
            logger.warning("PINNED MENU pin failed (message %s)", message_id)
            return False

    async def _safe_pins(self, channel: PinnedMenuChannel) -> list[Any]:
        try:
            return list(await channel.pins())
        except Exception:
            logger.warning("PINNED MENU pin lookup failed — best-effort")
            return []

    @staticmethod
    def _carries_marker(message: Any, marker: str) -> bool:
        """Whether a pinned message carries a component of the marker namespace."""
        for custom_id in _walk_custom_ids(message):
            if custom_id.startswith(marker):
                return True
        return False


def _walk_custom_ids(message: Any) -> list[str]:
    """Walk the components of a message (V2 layout or classic view)."""
    ids: list[str] = []

    def _items_of(component: Any) -> None:
        custom_id = getattr(component, "custom_id", None)
        if isinstance(custom_id, str):
            ids.append(custom_id)
        for attr in ("children", "components"):
            children = getattr(component, attr, None)
            if children and isinstance(children, (list, tuple)):
                for child in children:
                    _items_of(child)

    for component in getattr(message, "components", ()) or ():
        _items_of(component)
    return ids
