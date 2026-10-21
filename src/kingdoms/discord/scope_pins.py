"""The scope menus — every tree folder's home and admin, on the charte.

The channel tree gives each scope folder a ``général`` with a pinned
home menu and an ``admin`` with the management menu. Both follow the
menu template (title, description, sections of buttons, view id
footer); every button opens an ephemeral (the frozen-menu contract)
through the existing dispatches:

- ``home:open:<view>`` — the user-facing views (status, profile,
  games, the aoe2 game manager);
- ``admin:pin:modgames:section:<section>:<game>`` — the aoe2 admin
  sub-menus (civs, maps, pools: list, import, create, manage);
- the access and cross-guild flows keep their existing ids.

The menus resolve their channel by the stored tree id and render in
the guild's locale.
"""

from __future__ import annotations

import logging
from typing import Any

from kingdoms.core.ids import scope_view_key

logger = logging.getLogger("kingdoms.discord.scope_pins")


async def _locale_of(bot: Any, guild_id: str) -> str:
    """Resolve the guild's locale through the logs service (en fallback)."""
    logs = getattr(bot, "logs_service", None)
    if logs is None:
        return "en"
    try:
        return str(await logs.get_locale(guild_id) or "en")
    except Exception:
        return "en"


def _catalog(bot: Any) -> Any:
    """Resolve the message catalog (bot's or a fresh default)."""
    from kingdoms.core.services.i18n import MessageCatalog

    catalog = getattr(bot, "messages", None)
    if catalog is not None:
        return catalog
    config = getattr(bot, "config", None)
    return MessageCatalog(str(getattr(config, "config_dir", "config")))


def _tr(catalog: Any, key: str, locale: str, fallback: str = "") -> str:
    """Render one key; the fallback when unknown."""
    if catalog is None:
        return fallback or key
    rendered = str(catalog.render(key, locale))
    return rendered if rendered != key else (fallback or key)



_LOCK = "\U0001F512"  # the admin/read-only marker; the live permissions
# live on the Discord server (the overwrites), the notice describes
# what the channel holds


async def _tree_notice(guild_id: str, scope: str, catalog: Any = None, locale: str = "en", bot: Any = None) -> str:
    """One contextual line: the scope's channels (linked) and access.

    The homes explain what the bot provisions instead of offering a
    management view: the tree is declared (panels_scopes), the admins
    carry the ensure button. Each channel renders as a Discord link
    (``<#id>``) when its stored id resolves.
    """
    from kingdoms.discord.ui.panels import registered_scopes
    from kingdoms.discord.ui.panels_provisioning import stored_channel_id

    spec = next((sp for sp in registered_scopes() if sp.scope == scope), None)
    if spec is None:
        return ""
    lines: list[str] = [
        _tr(catalog, "ui.tree.channels_heading", locale, "**Salons** (g\u00e9n\u00e9r\u00e9s par le bot) :")
    ]
    raw_channels = _declared_channel_meta(scope)
    for decl in spec.channels:
        meta = raw_channels.get(decl.key, {})
        label = _tr(catalog, decl.label_key, locale, decl.label_key.rsplit(".", 1)[-1])
        # the description rides the i18n catalog (ui.panels.channels.<key>_description)
        description_key = str(meta.get("description", ""))
        description = _tr(catalog, description_key, locale, "") if description_key else ""
        lock = _LOCK if decl.access == "admin" else ""
        try:
            channel_id = await stored_channel_id(guild_id, scope, decl.key)
        except Exception:
            channel_id = None
        if channel_id:
            where = f"<#{channel_id}>{lock} \u2014 {description}" if description else f"<#{channel_id}>{lock}"
        else:
            base = f"**{label}**{lock} (\u00e0 cr\u00e9er)"
            where = f"{base} \u2014 {description}" if description else base
        lines.append(f"\u2022 {where}")
    role_lines = _scope_role_lines(guild_id, scope, bot, catalog=catalog, locale=locale)
    if role_lines:
        lines.append("")
        lines.append(
            _tr(catalog, "ui.tree.roles_heading", locale, "**R\u00f4les** (g\u00e9n\u00e9r\u00e9s par le bot) :")
        )
        lines.extend(role_lines)
    return "\n".join(lines)


def _scope_role_lines(
    guild_id: str, scope: str, bot: Any = None, catalog: Any = None, locale: str = "en"
) -> list[str]:
    """Return the scope's roles: one bullet each, a mention and its use.

    The platform declares the bot-admins role per guild; a mod's roles
    ride its own declaration and join the same notice.
    """
    def _t(key: str, fallback: str) -> str:
        if catalog is None:
            return fallback
        rendered = str(catalog.render(key, locale))
        return rendered if rendered != key else fallback

    use = _t("ui.tree.bot_admins_role", "les op\u00e9rateurs du bot : administrent la plateforme")
    return [
        f"\u2022 <@&{role_id}> \u2014 {use}"
        for role_id in _bot_admins_role_ids(guild_id, bot)
    ] or [
        "\u2022 **bot-admins** \u2014 "
        + _t(
            "ui.tree.bot_admins_role_pending",
            "les op\u00e9rateurs du bot (le r\u00f4le est cr\u00e9\u00e9 au premier sync)",
        )
    ]


def _bot_admins_role_ids(guild_id: str, bot: Any = None) -> list[str]:
    """Resolve the guild's bot-admins role ids (by name, live guild)."""
    guild = getattr(bot, "get_guild", lambda _g: None)(int(guild_id)) if bot and guild_id.isdigit() else None
    if guild is None:
        return []
    return [str(r.id) for r in getattr(guild, "roles", ()) if r.name == "bot-admins"]


def _declared_channel_meta(scope: str) -> dict[str, dict[str, Any]]:
    """Return the scope's channel declarations' raw metadata (descriptions)."""
    from kingdoms.discord.ui.panels_scopes import load_scopes_config

    raw = load_scopes_config().get(scope, {})
    return {str(c.get("key")): c for c in raw.get("channels", []) or []}



def _scope_channel(scope: str, key: str) -> Any:
    """Build a resolver for one tree channel (stored id only)."""

    async def _resolve(guild_id: str) -> str | None:
        from kingdoms.discord.ui.panels_provisioning import stored_channel_id

        return await stored_channel_id(guild_id, scope, key)

    return _resolve


# --- the per-scope home menus (the redesigned charte) ----------------


def register_scope_pins(bot: Any, scopes: tuple[str, ...]) -> list[Any]:
    """Declare (and register) every scope's home and admin pins.

    Idempotent: re-registering returns the already-registered specs
    (the static-pin store keeps the first per key).
    """
    from kingdoms.discord.static_pins import StaticPinnedView, register_static_pin

    registry = getattr(bot, "message_registry", None)
    specs: list[Any] = []
    # scope pin -> declared view id: the menus render from the registry
    # (views_declared), the legacy builders only serve the scopes no
    # declared view covers yet.
    declared_for = {
        "root": {"home": "menu:home", "legacy": "view:home"},
        "bot": {"admin": "menu:admin", "legacy": "admin-panel"},
        "games": {"home": "menu:games:home", "admin": "menu:games:admin"},
        "games/aoe2": {"home": "menu:games:aoe2", "admin": "menu:game:aoe2:admin"},
        "mods": {"home": "menu:mods:home", "admin": "menu:mods:admin"},
    }
    for scope in scopes:
        pins = declared_for.get(scope, {})
        if "home" in pins or (scope not in ("root", "bot") and "home" not in pins):
            home_key = scope_view_key(scope, "home")
            home_view_id = pins.get("home")
            # every scope declares its home menu — the legacy builders are gone
            builder = _declared_menu_builder(bot, home_view_id or "menu:home")
            specs.append(
                register_static_pin(
                    StaticPinnedView(
                        key=home_key,
                        mark_suffix=home_key,
                        legacy_key=pins.get("legacy", ""),
                        resolve_channel=_scope_channel(scope, "general"),
                        build_layout=builder,
                        registry=registry,
                        scope=scope,
                    )
                )
            )
        if scope != "root" and "admin" not in ("root",):
            admin_key = scope_view_key(scope, "admin")
            admin_view_id = pins.get("admin")
            builder = _declared_menu_builder(bot, admin_view_id or "menu:admin")
            specs.append(
                register_static_pin(
                    StaticPinnedView(
                        key=admin_key,
                        mark_suffix=admin_key,
                        legacy_key=pins.get("legacy", ""),
                        resolve_channel=_scope_channel(scope, "admin"),
                        build_layout=builder,
                        scope=scope,
                        registry=registry,
                    )
                )
            )
    return specs


def _declared_menu_builder(bot: Any, view_id: str) -> Any:
    """Build a declared menu's pin builder (registry-driven layout)."""
    from kingdoms.discord.views_routing import build_declared_menu

    inner = build_declared_menu(bot, view_id)

    async def _resolve_and_build(guild_id: str) -> Any:
        return await inner(guild_id)

    return _resolve_and_build


__all__ = ["register_scope_pins"]
