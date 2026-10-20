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

import discord

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
    rendered = catalog.render(key, locale)
    return rendered if rendered != key else (fallback or key)



_ACCESS_LABELS = {
    "public": "public (lecture + écriture pour tous)",
    "readonly": "lecture seule (menus épinglés du bot uniquement)",
    "admin": "admin (lecture + écriture pour les admins du bot)",
}


async def _tree_notice(guild_id: str, scope: str, catalog: Any = None, locale: str = "en") -> str:
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
    lines: list[str] = ["\U0001f4cb **Salons de ce périmètre** (générés par le bot) :"]
    for decl in spec.channels:
        label = _tr(catalog, decl.label_key, locale, decl.label_key.rsplit(".", 1)[-1])
        access = _ACCESS_LABELS.get(decl.access, decl.access)
        try:
            channel_id = await stored_channel_id(guild_id, scope, decl.key)
        except Exception:
            channel_id = None
        where = f"<#{channel_id}>" if channel_id else label
        shown = f"{where} — {access}" if channel_id else f"{label} — {access} (à créer)"
        lines.append(f"• {shown}")
    return "\n".join(lines)



def _scope_channel(scope: str, key: str) -> Any:
    """Build a resolver for one tree channel (stored id only)."""

    async def _resolve(guild_id: str) -> str | None:
        from kingdoms.discord.ui.panels_provisioning import stored_channel_id

        return await stored_channel_id(guild_id, scope, key)

    return _resolve


# --- the per-scope home menus (the redesigned charte) ----------------


def _home_builder(bot: Any, scope: str) -> Any:
    """Build a scope's home menu: charte + the scope's own buttons."""

    async def _build(guild_id: str) -> discord.ui.LayoutView:
        from kingdoms.discord.ui.menu_template import MenuSection, MenuSpec, build_menu

        catalog = _catalog(bot)
        locale = await _locale_of(bot, guild_id)
        prefix = f"ui.homes.{scope.replace('/', '_')}"
        title = _tr(catalog, f"{prefix}_home_title", locale)
        description = _tr(catalog, f"{prefix}_home_description", locale)
        sections: tuple[MenuSection, ...] = ()
        if scope == "root":
            sections = (
                MenuSection(
                    heading_key=_tr(catalog, "ui.homes.heading_navigate", locale, "Navigation"),
                    buttons=(
                        ("home:open:status", _tr(catalog, "ui.homes.btn_status", locale), "📊"),
                        ("home:open:profile", _tr(catalog, "ui.homes.btn_profile", locale), "🪪"),
                        ("home:open:games", _tr(catalog, "ui.homes.btn_games", locale), "🎮"),
                        ("home:open:mods", _tr(catalog, "ui.homes.btn_mods", locale), "🧩"),
                    ),
                ),
            )
        elif scope == "games":
            sections = (
                MenuSection(
                    heading_key=_tr(catalog, "ui.homes.heading_navigate", locale, "Navigation"),
                    buttons=(
                        ("home:open:games", _tr(catalog, "ui.homes.btn_games", locale), "🎮"),
                    ),
                ),
            )
        elif scope == "games/aoe2":
            sections = (
                MenuSection(
                    heading_key=_tr(catalog, "ui.homes.heading_data", locale, "Données"),
                    buttons=(
                        ("home:open:guild-games:aoe2", _tr(catalog, "ui.homes.btn_aoe2", locale), "⚔️"),
                        ("home:open:live", _tr(catalog, "ui.homes.btn_live", locale), "📡"),
                    ),
                ),
            )
        elif scope == "mods":
            sections = (
                MenuSection(
                    heading_key=_tr(catalog, "ui.homes.heading_navigate", locale, "Navigation"),
                    buttons=(
                        ("home:open:mods", _tr(catalog, "ui.homes.btn_mods", locale), "🧩"),
                    ),
                ),
            )
        return build_menu(
            MenuSpec(
                title=title,
                description=description,
                view_id="view:home" if scope == "root" else scope_view_key(scope, "home"),
                sections=sections,
                extra_lines=(await _tree_notice(guild_id, scope, catalog, locale),),
            )
        )

    return _build


# --- the per-scope admin menus (the redesigned charte) ----------------


def _admin_builder(bot: Any, scope: str) -> Any:
    """Build a scope's admin menu: charte + the scope's own buttons."""

    async def _build(guild_id: str) -> discord.ui.LayoutView:
        from kingdoms.discord.ui.menu_template import MenuSection, MenuSpec, build_menu

        catalog = _catalog(bot)
        locale = await _locale_of(bot, guild_id)
        if scope == "games/aoe2":
            # the three sub-menus the sessions designed: civs, maps, pools
            return build_menu(
                MenuSpec(
                    title=_tr(catalog, "ui.homes.games_aoe2_admin_title", locale, "⚔️ Age of Empires II — admin"),
                    description=_tr(
                        catalog,
                        "ui.homes.games_aoe2_admin_description",
                        locale,
                        "Les données du jeu : civilisations, maps et map pools.",
                    ),
                    view_id=scope_view_key(scope, "admin"),
                    sections=(
                        MenuSection(
                            heading_key=_tr(catalog, "ui.homes.heading_data", locale, "Données"),
                            buttons=(
                                (
                                    "admin:pin:modgames:section:civs:aoe2",
                                    _tr(catalog, "ui.homes.btn_civs", locale, "Civs"),
                                    "🏛️",
                                ),
                                (
                                    "admin:pin:modgames:section:maps:aoe2",
                                    _tr(catalog, "ui.homes.btn_maps", locale, "Maps"),
                                    "🗺️",
                                ),
                                (
                                    "admin:pin:modgames:section:pools:aoe2",
                                    _tr(catalog, "ui.homes.btn_pools", locale, "Map pools"),
                                    "🧱",
                                ),
                            ),
                        ),
                        MenuSection(
                            heading_key=_tr(catalog, "ui.homes.heading_actions", locale, "Actions"),
                            buttons=(
                                (
                                    "admin:pin:modgames:maps:import:guild:aoe2",
                                    _tr(catalog, "ui.homes.btn_add_map", locale, "Ajouter une map"),
                                    "🌟",
                                ),
                                (
                                    "admin:pin:modgames:pools:create:aoe2",
                                    _tr(catalog, "ui.homes.btn_create_pool", locale, "Créer un map pool"),
                                    "🧱",
                                ),
                            ),
                        ),
                    ),
                )
            )
        if scope in ("games", "mods"):
            # the perimeter view + the provisioning ensure; the access
            # request/revoke flow is gone (everything is active by default)
            kind = "games" if scope == "games" else "mods"
            list_view = "games" if scope == "games" else "mods"
            return build_menu(
                MenuSpec(
                    title=_tr(catalog, f"ui.homes.{kind}_admin_title", locale, f"{kind} — admin"),
                    description=_tr(
                        catalog,
                        f"ui.homes.{kind}_admin_description",
                        locale,
                        "Les périmètres du serveur et leur configuration.",
                    ),
                    extra_lines=(await _tree_notice(guild_id, scope, catalog, locale),),
                    view_id=scope_view_key(scope, "admin"),
                    sections=(
                        MenuSection(
                            heading_key=_tr(catalog, "ui.homes.heading_perimeters", locale, "Périmètres"),
                            buttons=(
                                (
                                    f"home:open:{list_view}",
                                    _tr(catalog, "ui.homes.btn_list", locale, "Liste"),
                                    "\U0001f4cb",
                                ),
                            ),
                        ),
                        MenuSection(
                            heading_key=_tr(catalog, "ui.homes.heading_configuration", locale, "Configuration"),
                            buttons=(
                                (
                                    f"admin:tree:ensure:{scope}",
                                    _tr(catalog, "ui.homes.btn_ensure", locale, "Tout regénérer"),
                                    "\U0001f527",
                                ),
                            ),
                        ),
                    ),
                )
            )
        # bot: the operation panel keeps its dedicated surfaces (language,
        # channels, roles) through the shared admin dispatch
        return build_menu(
            MenuSpec(
                title=_tr(catalog, "ui.homes.bot_admin_title", locale, "🤖 Bot — admin"),
                description=_tr(
                    catalog,
                    "ui.homes.bot_admin_description",
                    locale,
                    "L'exploitation du bot : langue, salons, rôles, jeux et mods.",
                ),
                view_id=scope_view_key(scope, "admin"),
                sections=(
                    MenuSection(
                        buttons=(
                            ("home:open:admin", _tr(catalog, "ui.homes.btn_admin", locale, "Admin"), "🛠️"),
                        ),
                    ),
                ),
            )
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
    # scope pin -> declared view id: the menus render from the registry
    # (views_declared), the legacy builders only serve the scopes no
    # declared view covers yet.
    declared_for = {
        "root": {"home": "menu:home"},
        "bot": {"admin": "menu:admin"},
        "games": {"home": "menu:games:home", "admin": "menu:games:admin"},
        "games/aoe2": {"home": "menu:games:aoe2", "admin": "menu:game:aoe2:admin"},
        "mods": {"home": "menu:mods:home", "admin": "menu:mods:admin"},
    }
    for scope in scopes:
        pins = declared_for.get(scope, {})
        if "home" in pins or (scope not in ("root", "bot") and "home" not in pins):
            home_key = scope_view_key(scope, "home")
            home_view_id = pins.get("home")
            builder = (
                _declared_menu_builder(bot, home_view_id)
                if home_view_id is not None
                else _home_builder(bot, scope)
            )
            specs.append(
                register_static_pin(
                    StaticPinnedView(
                        key=home_key,
                        mark_suffix=home_key,
                        resolve_channel=_scope_channel(scope, "general"),
                        build_layout=builder,
                        registry=registry,
                    )
                )
            )
        if scope != "root" and "admin" not in ("root",):
            admin_key = scope_view_key(scope, "admin")
            admin_view_id = pins.get("admin")
            builder = (
                _declared_menu_builder(bot, admin_view_id)
                if admin_view_id is not None
                else _admin_builder(bot, scope)
            )
            specs.append(
                register_static_pin(
                    StaticPinnedView(
                        key=admin_key,
                        mark_suffix=admin_key,
                        resolve_channel=_scope_channel(scope, "admin"),
                        build_layout=builder,
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
