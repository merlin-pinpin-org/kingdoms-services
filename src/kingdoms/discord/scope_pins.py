"""The scope menus: every tree folder carries its home and admin pins.

The channel tree (docs/architecture/channel-tree.md) gives each scope
folder a ``général`` with a pinned home menu and an ``admin`` with the
management menu. This module declares those pins as StaticPinnedViews
so the shared cycle owns them (create, refresh, re-pin, heal) — the
boot's surface pass ensures them all synchronously right after the
tree is provisioned.

Every menu resolves its channel by the **stored tree id** (never by
the translated name) and renders in the guild's locale.
"""

from __future__ import annotations

import logging
from typing import Any

import discord

logger = logging.getLogger("kingdoms.discord.scope_pins")

_pin_specs: list[Any] = []


def _scope_channel(scope: str, key: str) -> Any:
    """Build a resolver for one tree channel (stored id first, never name)."""

    async def _resolve(guild_id: str) -> str | None:
        from kingdoms.discord.ui.panels_provisioning import stored_channel_id

        return await stored_channel_id(guild_id, scope, key)

    return _resolve


def _locale_of(bot: Any, guild_id: str) -> Any:
    """Resolve the guild's locale through the logs service (en fallback)."""

    async def _locale() -> str:
        logs = getattr(bot, "logs_service", None)
        if logs is None:
            return "en"
        try:
            return str(await logs.get_locale(guild_id) or "en")
        except Exception:
            return "en"

    return _locale()


def _home_builder(bot: Any, scope: str, title_key: str, description_key: str) -> Any:
    """Build a scope's home menu: description + the scope's own buttons."""

    async def _build(guild_id: str) -> discord.ui.LayoutView:
        from kingdoms.core.services.i18n import MessageCatalog
        from kingdoms.discord.pinned_marks import pinned_mark

        catalog = getattr(bot, "messages", None) or MessageCatalog(
            str(getattr(getattr(bot, "config", None), "config_dir", "config"))
        )
        locale = await _locale_of(bot, guild_id)()
        title = catalog.render(title_key, locale)
        description = catalog.render(description_key, locale)
        view_id = f"view:{scope}:home" if scope != "root" else "view:home"
        view = discord.ui.LayoutView(timeout=None)
        view.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(f"# {title}\n{description}"),
                discord.ui.TextDisplay(pinned_mark(view_id)),
            )
        )
        return view

    return _build


def _admin_builder(bot: Any, scope: str) -> Any:
    """Build a scope's admin menu — the shared admin panel today.

    The per-scope admin surfaces (games admin, aoe2 admin, mods admin)
    all render the same management panel for now; the sections evolve
    per scope later without touching the cycle.
    """

    async def _build(guild_id: str) -> discord.ui.LayoutView:
        from kingdoms.discord.admin_panel_dynamic import build_pin_main_menu

        logs = getattr(bot, "logs_service", None)
        if logs is None:
            return discord.ui.LayoutView(timeout=None)
        locale = await _locale_of(bot, guild_id)()
        return await build_pin_main_menu(
            logs,
            guild_id,
            getattr(bot, "messages", None),
            locale,
            getattr(bot, "admin_channel_service", None),
        )

    return _build


def register_scope_pins(bot: Any, scopes: tuple[str, ...]) -> list[Any]:
    """Declare (and register) every scope's home and admin pins.

    Idempotent: re-registering returns the already-registered specs
    (the static-pin store keeps the first per key).
    """
    from kingdoms.discord.static_pins import StaticPinnedView, register_static_pin

    registry = getattr(bot, "message_registry", None)
    specs: list[Any] = []
    for scope in scopes:
        home_key = "view:home" if scope == "root" else f"view:{scope}:home"
        admin_key = f"view:{scope}:admin"
        title_key = f"ui.homes.{scope.replace('/', '_')}_home_title"
        description_key = f"ui.homes.{scope.replace('/', '_')}_home_description"
        home_spec = StaticPinnedView(
            key=home_key,
            mark_suffix=home_key,
            resolve_channel=_scope_channel(scope, "general"),
            build_layout=_home_builder(bot, scope, title_key, description_key),
            registry=registry,
        )
        admin_spec = StaticPinnedView(
            key=admin_key,
            mark_suffix=admin_key,
            resolve_channel=_scope_channel(scope, "admin"),
            build_layout=_admin_builder(bot, scope),
            registry=registry,
        )
        specs.append(register_static_pin(home_spec))
        specs.append(register_static_pin(admin_spec))
    _pin_specs[:] = specs
    return specs


__all__ = ["register_scope_pins"]
