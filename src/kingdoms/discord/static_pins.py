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
- **mark**: every rendered layout carries its ``fixe:<suffix>`` id;
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

logger = logging.getLogger("kingdoms.static_pins")

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

    async def resolve_message_id(self, guild_id: str) -> str | None:
        """Resolve the registered pin id (registry first, memory fallback)."""
        if self.registry is not None:
            try:
                registered = await self.registry.resolve("discord", self.key, guild_id)
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
        if isinstance(component, (list, tuple)):
            return [walk(c) for c in component]
        if hasattr(component, "custom_id"):
            return {
                "id": getattr(component, "custom_id", None),
                "label": getattr(component, "label", None),
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
        await message.pin(reason=f"kingdoms: pinned {key}")
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
    if message_id:
        try:
            message = await channel.fetch_message(int(message_id))
            layout = await spec.build_layout(guild_id)
            fingerprint = _layout_fingerprint(layout)
            if _LAST_RENDER.get((spec.key, guild_id)) == fingerprint:
                await _safe_repin(message, spec.key)
                return False
            await message.edit(view=layout)
            _LAST_RENDER[(spec.key, guild_id)] = fingerprint
            await _safe_repin(message, spec.key)
            return False
        except Exception:
            logger.info("%s pin is gone — recreating", spec.key, exc_info=True)

    superseded = await _collect_superseded(channel, message_id)
    if superseded and spec.delete_when_superseded:
        from kingdoms.discord.pinned_marks import is_pinned_view

        for stale in superseded:
            if not is_pinned_view(stale):
                continue
            try:
                await stale.unpin(reason=f"kingdoms: superseded {spec.key}")
                await stale.delete()
            except Exception:
                logger.info("%s superseded cleanup skipped — best-effort", spec.key, exc_info=True)

    layout = await spec.build_layout(guild_id)
    message = await channel.send(view=layout)
    await message.pin(reason=f"kingdoms: pinned {spec.key}")
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


async def heal_static_pins(bot: discord.Client, guild_id: str) -> int:
    """Re-ensure every registered static pin (channel-delete heal)."""
    healed = 0
    for spec in registered_static_pins():
        try:
            if await ensure_static_pin(bot, spec, guild_id):
                healed += 1
        except Exception:
            logger.warning("%s heal failed — best-effort", spec.key, exc_info=True)
    return healed
