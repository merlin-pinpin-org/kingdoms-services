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
                    ),
                ),
            )
        elif scope == "mods":
            sections = (
                MenuSection(
                    heading_key=_tr(catalog, "ui.homes.heading_navigate", locale, "Navigation"),
                    buttons=(
                        ("home:open:games", _tr(catalog, "ui.homes.btn_mods", locale), "🧩"),
                    ),
                ),
            )
        return build_menu(
            MenuSpec(
                title=title,
                description=description,
                view_id="view:home" if scope == "root" else scope_view_key(scope, "home"),
                sections=sections,
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
                    ),
                )
            )
        if scope in ("games", "mods"):
            # the perimeter management: granted list, access request,
            # channels and roles (the shared admin dispatches)
            kind = "games" if scope == "games" else "mods"
            extra = (
                (
                    _tr(
                        catalog,
                        "ui.homes.mods_doc_line",
                        locale,
                        "📖 Documentation des mods : https://github.com/merlin-pinpin-org/kingdoms/tree/main/docs/MODS",
                    ),
                )
                if scope == "mods"
                else ()
            )
            return build_menu(
                MenuSpec(
                    title=_tr(catalog, f"ui.homes.{kind}_admin_title", locale, f"{kind} — admin"),
                    description=_tr(
                        catalog,
                        f"ui.homes.{kind}_admin_description",
                        locale,
                        "Les périmètres accordés, les demandes d'accès et la configuration.",
                    ),
                    extra_lines=extra,
                    view_id=scope_view_key(scope, "admin"),
                    sections=(
                        MenuSection(
                            heading_key=_tr(catalog, "ui.homes.heading_perimeters", locale, "Périmètres"),
                            buttons=(
                                ("home:open:games", _tr(catalog, "ui.homes.btn_list", locale, "Liste"), "📋"),
                                (
                                    f"admin:access:open:{guild_id}",
                                    _tr(catalog, "ui.homes.btn_request_access", locale, "Demander l'accès"),
                                    "✉️",
                                ),
                            ),
                        ),
                        MenuSection(
                            heading_key=_tr(catalog, "ui.homes.heading_configuration", locale, "Configuration"),
                            buttons=(
                                (
                                    "admin:pin:button:roles",
                                    _tr(catalog, "ui.homes.btn_channels_roles", locale, "Salons & rôles"),
                                    "🛠️",
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
    for scope in scopes:
        if scope not in ("root", "bot"):  # root's home is the legacy view:home spec
            home_key = scope_view_key(scope, "home")
            specs.append(
                register_static_pin(
                    StaticPinnedView(
                        key=home_key,
                        mark_suffix=home_key,
                        resolve_channel=_scope_channel(scope, "general"),
                        build_layout=_home_builder(bot, scope),
                        registry=registry,
                    )
                )
            )
        if scope != "root":  # the root has no admin channel of its own
            admin_key = scope_view_key(scope, "admin")
            specs.append(
                register_static_pin(
                    StaticPinnedView(
                        key=admin_key,
                        mark_suffix=admin_key,
                        resolve_channel=_scope_channel(scope, "admin"),
                        build_layout=_admin_builder(bot, scope),
                        registry=registry,
                    )
                )
            )
    return specs


__all__ = ["register_scope_pins"]
