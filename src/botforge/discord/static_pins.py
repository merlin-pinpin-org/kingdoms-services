"""Static pinned views: one declarative registry, one ensure cycle.

A static pinned view (home menu, admin panel, boot status) is a pin
whose content the bot owns in a dedicated channel. Each registers a
``StaticPinnedView`` spec once at wiring; the shared cycle then owns
everything:

- **channel**: resolved through its managed-channel service (created
  on demand — a deleted channel comes back);
- **pin**: created when missing, edited in place to the current
  layout when it lives (a boot with a changed surface updates the
  pin), re-pinned when unpinned;
- **mark**: every rendered layout carries its view id;
- **heal**: the channel-delete event re-ensures every registered view
  — the pin follows the recreated channel instead of staying lost.

The message registry (Mongo, restart-proof) tracks each pin by its
logical key; an in-memory fallback covers the unwired store.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import discord

logger = logging.getLogger("botforge.static_pins")

BuildLayout = Callable[[str], Awaitable[Any]]
ResolveChannel = Callable[[str], Awaitable[str | None]]


@dataclass(frozen=True)
class StaticPinnedView:
    """One static pinned surface, declared once at wiring.

    ``delete_when_superseded`` (default True): a superseded pinned
    message is deleted — the cycle keeps one pin per view. A surface
    that must keep its history (the boot status log trail) declares
    False: the old pins are merely unpinned, the messages stay.
    """

    key: str
    mark_suffix: str
    resolve_channel: ResolveChannel
    build_layout: BuildLayout
    registry: Any = None
    delete_when_superseded: bool = True
    legacy_key: str = ""
    scope: str = ""

    async def resolve_message_id(self, guild_id: str) -> str | None:
        """Resolve the registered pin id (registry first, memory fallback)."""
        if self.registry is not None:
            try:
                registered = await self.registry.resolve("discord", self.key, guild_id)
                if registered is None and self.legacy_key:
                    registered = await self.registry.resolve("discord", self.legacy_key, guild_id)
                if registered is not None:
                    return str(registered.message_id)
            except Exception:
                logger.warning("%s registry resolve failed — best-effort", self.key)
        return _MEMORY_STORE.get((self.key, guild_id))

    async def register_message_id(self, guild_id: str, channel_id: str, message_id: str) -> None:
        """Persist the pin id (registry + memory fallback)."""
        _MEMORY_STORE[(self.key, guild_id)] = message_id
        if self.registry is not None:
            try:
                await self.registry.register(
                    platform="discord",
                    message_key=self.key,
                    entity_id=guild_id,
                    channel_id=channel_id,
                    message_id=message_id,
                    guild_id=guild_id,
                )
            except Exception:
                logger.warning("%s registry register failed — best-effort", self.key)


_SPEC_STORE: dict[str, StaticPinnedView] = {}
_MEMORY_STORE: dict[tuple[str, str], str] = {}
_LAST_RENDER: dict[tuple[str, str], str] = {}


def _layout_fingerprint(layout: Any) -> str:
    """Hash a layout's rendered payload (components' ids and labels)."""
    import hashlib
    import json

    def walk(component: Any) -> Any:
        """Reduce one component tree to its ids and labels."""
        if isinstance(component, (list, tuple)):
            return [walk(c) for c in component]
        if hasattr(component, "custom_id"):
            return {
                "id": getattr(component, "custom_id", None),
                "label": getattr(component, "label", None),
                # the disabled state is part of the change: the
                # auto-injected entries flip enabled/grayed with the
                # guild's install state (a stale pin is a stale state)
                "disabled": bool(getattr(component, "disabled", False)),
                "children": walk(getattr(component, "children", []) or []),
            }
        if hasattr(component, "options"):
            return [str(getattr(o, "label", o)) for o in component.options]
        if hasattr(component, "children"):
            return walk(component.children)
        return str(component)

    payload = json.dumps(walk(layout), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def _safe_repin(message: Any, key: str) -> None:
    """Re-pin a live pin, best-effort (Discord's edit quota stays cold)."""
    try:
        await message.pin(reason=f"botforge: pinned {key}")
    except Exception:
        logger.debug("%s re-pin skipped — best-effort", key, exc_info=True)


def register_static_pin(spec: StaticPinnedView) -> StaticPinnedView:
    """Register one static pinned view (idempotent; the live spec wins).

    Re-registering the same key returns the already-registered spec:
    the memory fallback store is module-level, so the pin ids survive
    a re-declaration (the builders are rebuilt at each wiring pass).
    """
    existing = _SPEC_STORE.get(spec.key)
    if existing is not None:
        return existing
    _SPEC_STORE[spec.key] = spec
    return spec


def reset_static_pins() -> None:
    """Clear the registry (test isolation: each suite starts clean)."""
    _SPEC_STORE.clear()
    _MEMORY_STORE.clear()
    _LAST_RENDER.clear()


def registered_static_pins() -> tuple[StaticPinnedView, ...]:
    """List every registered static pinned view."""
    return tuple(_SPEC_STORE.values())


def unregister_static_pin(key: str) -> None:
    """Drop one spec from the registry (an uninstalled surface).

    The pin ids stay in the message registry until ``remove_static_pin``
    forgets them; unregistering only stops the ensure cycle from
    re-creating the surface.
    """
    _SPEC_STORE.pop(key, None)


async def remove_static_pin(bot: discord.Client, spec: StaticPinnedView, guild_id: str) -> bool:
    """Remove one static pin: unpin, delete the message, forget the id.

    The uninstall path: the surface goes away with its mod — the pin
    must not survive the item it presents (nor be healed back).
    """
    message_id = await spec.resolve_message_id(guild_id)
    channel_id = await spec.resolve_channel(guild_id)
    removed = False
    if message_id and channel_id and guild_id.isdigit() and channel_id.isdigit():
        guild = bot.get_guild(int(guild_id))
        channel = guild.get_channel(int(channel_id)) if guild else None
        if channel is not None and hasattr(channel, "fetch_message"):
            try:
                message = await channel.fetch_message(int(message_id))
                try:
                    await message.unpin(reason=f"botforge: unpinned {spec.key}")
                except Exception:
                    logger.debug("%s unpin skipped \u2014 best-effort", spec.key, exc_info=True)
                await message.delete()
                removed = True
            except discord.NotFound:
                removed = True
            except Exception:
                logger.info("%s removal degraded \u2014 best-effort", spec.key, exc_info=True)
    if spec.registry is not None:
        try:
            await spec.registry.forget("discord", spec.key, guild_id)
        except Exception:
            logger.warning("%s registry forget failed \u2014 best-effort", spec.key)
    _MEMORY_STORE.pop((spec.key, guild_id), None)
    _LAST_RENDER.pop((spec.key, guild_id), None)
    return removed


async def _refresh_existing_pin(
    channel: Any, spec: StaticPinnedView, guild_id: str, message_id: str
) -> bool | None:
    """Refresh the live pin in place; None when it must be recreated.

    The 404 (the pin's message was deleted) is the NORMAL heal path —
    logged as a line, never a stacktrace (the pin is recreated right
    after; the 2026-11-07 noise report).
    """
    try:
        message = await channel.fetch_message(int(message_id))
        layout = await spec.build_layout(guild_id)
        fingerprint = _layout_fingerprint(layout)
        if _LAST_RENDER.get((spec.key, guild_id)) != fingerprint:
            await message.edit(view=layout)
            _LAST_RENDER[(spec.key, guild_id)] = fingerprint
        await _safe_repin(message, spec.key)
        return True
    except discord.NotFound:
        logger.info("%s pin is gone — recreating", spec.key)
        return None
    except Exception:
        logger.info("%s pin is unreadable — recreating", spec.key, exc_info=True)
        return None


async def ensure_static_pin(bot: discord.Client, spec: StaticPinnedView, guild_id: str) -> bool:
    """Ensure one static pin lives in its channel (create, update, heal).

    Returns True when the pin was (re-)created, False when the existing
    one was refreshed (or the step degraded quietly). The channel is
    resolved first — a deleted channel is recreated by its service,
    and the pin follows.
    """
    channel_id = await spec.resolve_channel(guild_id)
    if channel_id is None:
        return False
    guild = bot.get_guild(int(guild_id)) if guild_id.isdigit() else None
    channel = guild.get_channel(int(channel_id)) if channel_id.isdigit() and guild else None
    if channel is None or not hasattr(channel, "fetch_message") or not hasattr(channel, "send"):
        return False

    message_id = await spec.resolve_message_id(guild_id)
    if message_id and await _refresh_existing_pin(channel, spec, guild_id, message_id) is True:
        return False

    superseded = await _collect_superseded(channel, message_id)
    if superseded and spec.delete_when_superseded:
        from botforge.discord.pinned_marks import is_pinned_view

        for stale in superseded:
            if not is_pinned_view(stale):
                continue
            try:
                await stale.unpin(reason=f"botforge: superseded {spec.key}")
                await stale.delete()
            except Exception:
                logger.info("%s superseded cleanup skipped — best-effort", spec.key, exc_info=True)

    layout = await spec.build_layout(guild_id)
    # silent: a bulk ensure pins many views in a row — no notification storm
    message = await channel.send(view=layout, silent=True)
    await message.pin(reason=f"botforge: pinned {spec.key}")
    await spec.register_message_id(guild_id, str(channel_id), str(message.id))
    return True


async def _collect_superseded(channel: Any, current_id: str | None) -> list[Any]:
    """List the view's superseded messages in the channel (best-effort).

    The pins of the channel minus the current one; unreadable pins
    degrade to an empty list — the cycle never fails on this.
    """
    try:
        pinned = await channel.pins()
    except Exception:
        return []
    return [m for m in pinned if str(m.id) != str(current_id or "")]

