"""Pinned-view helpers: pin-aware answers, refreshes and the read-only state.

Three cross-cutting rules live here, once, for every pinned surface
(core pins and mod pins alike):

1. **A pin never moves.** A click on a pinned view must not edit the
   pinned message: actions answer **ephemerally** (or edit-in-place
   only the registered pin's *content* through the refresh path). The
   entry-point is :func:`answer_ephemeral` — a pin-aware wrapper over
   ``send_message(ephemeral=True)``.

2. **Pins refresh automatically.** After a parameter (or sub-parameter)
   or the guild locale changes, the pinned views must re-render. Each
   surface registers a refresh callback
   (:func:`register_pin_refresher`/:func:`refresh_registered_pins`);
   the persistent admin handlers call it after every mutation.

3. **Pinned channels are read-only by default.** The guild setting
   ``pinned_read_only`` (per managed channel category, plus mod-scoped
   entries) stores the intent; the channel platforms apply the Discord
   permission overwrite on every policy pass — the bot keeps writing
   through its own member overwrite (a pin is updated by the bot, the
   members read it).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import discord

logger = logging.getLogger("kingdoms.pinned_views")

PinRefresher = Callable[[str], Awaitable[None]]

_REFRESHERS: dict[str, PinRefresher] = {}


def register_pin_refresher(key: str, refresher: PinRefresher) -> None:
    """Register one surface's pin refresher (idempotent, last wins)."""
    _REFRESHERS[key] = refresher


async def refresh_registered_pins(guild_id: str) -> None:
    """Run every registered pin refresher for a guild (best-effort)."""
    for key, refresher in list(_REFRESHERS.items()):
        try:
            await refresher(guild_id)
        except Exception:
            logger.warning("pin refresh failed (%s, guild %s) — best-effort", key, guild_id, exc_info=True)


async def answer_ephemeral(interaction: discord.Interaction, message: str) -> None:
    """Answer a click ephemerally — a pinned view never moves."""
    try:
        await interaction.response.send_message(message, ephemeral=True)
    except Exception:
        logger.warning("ephemeral answer failed — best-effort", exc_info=True)


async def answer_ephemeral_view(interaction: discord.Interaction, view: discord.ui.LayoutView) -> None:
    """Answer a click with an ephemeral view — a pinned view never moves."""
    try:
        await interaction.response.send_message(view=view, ephemeral=True)
    except Exception:
        logger.warning("ephemeral view answer failed — best-effort", exc_info=True)


_SETTINGS_DB_RESOLVER: Callable[[], Any | None] | None = None


def set_settings_db_resolver(resolver: Callable[[], Any | None]) -> None:
    """Register the running bot's guild-settings seam (factory hookup)."""
    global _SETTINGS_DB_RESOLVER
    _SETTINGS_DB_RESOLVER = resolver


def _settings_db(interaction: object) -> Any | None:
    """Resolve the guild-settings seam from the running bot."""
    client = getattr(interaction, "client", None)
    logs = getattr(client, "logs_service", None)
    db = getattr(logs, "_db", None) if logs is not None else None
    if db is not None:
        return db
    return _SETTINGS_DB_RESOLVER() if _SETTINGS_DB_RESOLVER is not None else None


async def get_pinned_read_only(interaction_or_guild_id: object, category: str) -> bool:
    """Read the read-only intent for a pinned channel category.

    Default: read-only — the parameter reads ``pinned_read_only`` in
    the guild settings and falls back to ``True`` (every pinned channel
    starts read-only; the admin opens it from the panel).
    """
    if isinstance(interaction_or_guild_id, (str, int)):
        guild_id, db = str(interaction_or_guild_id), None
        if _SETTINGS_DB_RESOLVER is not None:
            try:
                db = _SETTINGS_DB_RESOLVER()
            except Exception:
                db = None
    else:
        guild_id = str(getattr(interaction_or_guild_id, "guild_id", "") or "")
        db = _settings_db(interaction_or_guild_id)
    state = await _read_state(db, guild_id)
    if state is None:
        return True
    return bool(state.get(category, True))


async def set_pinned_read_only(guild_id: str, category: str, read_only: bool, db: Any = None) -> None:
    """Persist the read-only intent for a pinned channel category."""
    settings = await _load_settings(db, guild_id)
    state = dict(settings.get("pinned_read_only") or {})
    state[category] = bool(read_only)
    settings["pinned_read_only"] = state
    settings["updated_at"] = settings.get("updated_at")
    await _save_settings(db, guild_id, settings)


async def _read_state(db: Any, guild_id: str) -> dict[str, Any] | None:
    if db is None or not guild_id:
        return None
    try:
        settings = await db.get_guild_settings(guild_id)
        return dict(settings.get("pinned_read_only") or {}) if settings else None
    except Exception:
        logger.warning("pinned read-only state read failed — best-effort", exc_info=True)
        return None


async def _load_settings(db: Any, guild_id: str) -> dict[str, Any]:
    if db is None or not guild_id:
        return {}
    try:
        return dict(await db.get_guild_settings(guild_id) or {})
    except Exception:
        logger.warning("guild settings read failed — best-effort", exc_info=True)
        return {}


async def _save_settings(db: Any, guild_id: str, settings: dict[str, Any]) -> None:
    if db is None or not guild_id:
        return
    try:
        await db.set_guild_settings(guild_id, settings)
    except Exception:
        logger.warning("guild settings write failed — best-effort", exc_info=True)


def read_only_overwrite(read_only: bool, base: discord.PermissionOverwrite) -> discord.PermissionOverwrite:
    """Return the overwrite with ``send_messages`` closed for @everyone."""
    return discord.PermissionOverwrite(**{**dict(base), "send_messages": not read_only})


async def apply_read_only_policy(
    guild: Any, channel: Any, category: str, guild_id: str, settings_db: Any = None
) -> None:
    """Apply the read-only intent to one channel (id-based, best-effort).

    Every member-facing overwrite keeps its visibility; only the write
    bit follows the stored intent. The bot member always keeps writing
    (the pin must stay refreshable). ``settings_db`` defaults to the
    running bot's logs seam — channel platforms call this on every
    policy pass, so a setting change propagates at the next resolution
    (or immediately through the panel's refresher).
    """
    try:
        read_only = (
            await get_pinned_read_only(guild_id, category)
            if settings_db is None
            else bool((await _read_state(settings_db, guild_id) or {}).get(category, True))
        )
        overwrite = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=not read_only,
            read_message_history=True,
        )
        if channel is not None and isinstance(channel, discord.TextChannel):
            await channel.set_permissions(
                guild.default_role,
                overwrite=overwrite,
                reason=f"kingdoms: pinned channel read-only policy ({category})",
            )
            member = getattr(guild, "me", None)
            if member is not None:
                await channel.set_permissions(
                    member,
                    overwrite=discord.PermissionOverwrite(
                        view_channel=True,
                        send_messages=True,
                        read_message_history=True,
                        manage_messages=True,
                    ),
                    reason=f"kingdoms: pinned channel bot keeps writing ({category})",
                )
    except Exception:
        logger.warning("read-only policy apply failed (%s) — best-effort", category, exc_info=True)


async def refresh_pin_by_marker(
    bot: Any, guild: Any, marker: str, builder: Callable[[str], Awaitable[discord.ui.LayoutView]]
) -> bool:
    """Re-render one pinned message in place (found by marker, id-based).

    The pin never moves: its layout is edited where it lives. Returns
    True when a pin was refreshed.
    """
    for channel in list(getattr(guild, "channels", ()) or ()):
        pins = None
        try:
            pins = await channel.pins()
        except Exception:
            logger.warning("pin lookup failed during refresh — best-effort", exc_info=True)
            continue
        for message in pins or ():
            ids: list[str] = []
            _walk_custom_ids(message, ids)
            if not any(cid.startswith(marker) for cid in ids):
                continue
            try:
                layout = await builder(str(guild.id))
                await message.edit(view=layout)
                return True
            except Exception:
                logger.warning("pin refresh by marker failed (%s) — best-effort", marker, exc_info=True)
                return False
    return False


def _walk_custom_ids(component: Any, ids: list[str]) -> None:
    """Collect the custom ids of a message's components (V2 or classic)."""
    custom_id = getattr(component, "custom_id", None)
    if isinstance(custom_id, str):
        ids.append(custom_id)
    for attr in ("children", "components"):
        children = getattr(component, attr, None)
        if children and isinstance(children, (list, tuple)):
            for child in children:
                _walk_custom_ids(child, ids)


async def refresh_or_edit(
    interaction: discord.Interaction,
    rebuild: Callable[[], Awaitable[discord.ui.LayoutView]],
    refresher_key: str | None = None,
) -> None:
    """Answer a click without ever moving a pinned view.

    On a pinned message the pin is re-rendered **in place** (the same
    message, the new state — it never moves nor duplicates); on a live
    sub-view message the view is edited as before — sub-menus keep
    their navigation.
    """
    message = getattr(interaction, "message", None)
    pinned = bool(getattr(message, "pinned", False))
    guild_id = str(getattr(interaction, "guild_id", "") or "")
    if pinned:
        view = await rebuild()
        try:
            await interaction.response.edit_message(view=view)
            return
        except Exception:
            logger.warning("pinned edit failed — falling back to refresh", exc_info=True)
    if pinned:
        await refresh_registered_pins(guild_id)
        return
    await interaction.response.edit_message(view=await rebuild())
