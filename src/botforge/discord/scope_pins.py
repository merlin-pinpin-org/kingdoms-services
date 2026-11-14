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

from botforge.core.ids import scope_view_key

logger = logging.getLogger("botforge.discord.scope_pins")


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
    from botforge.core.services.i18n import MessageCatalog

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
    from botforge.discord.ui.panels import registered_scopes
    from botforge.discord.ui.panels_provisioning import stored_channel_id

    spec = next((sp for sp in registered_scopes() if sp.scope == scope), None)
    if spec is None:
        return ""
    channels: list[str] = []
    for decl in spec.channels:
        label = _tr(catalog, decl.label_key, locale, decl.label_key.rsplit(".", 1)[-1])
        lock = _LOCK if decl.access == "admin" else ""
        try:
            channel_id = await stored_channel_id(guild_id, scope, decl.key)
        except Exception:
            channel_id = None
        if channel_id:
            channels.append(f"<#{channel_id}>{lock}")
        else:
            channels.append(f"**{label}**{lock} (\u00e0 cr\u00e9er)")
    lines: list[str] = []
    if channels:
        lines.append(_tr(catalog, "ui.tree.channels_heading", locale, "\U0001f4fa Salons :"))
        lines.extend(f"\u2022 {item}" for item in channels)
    roles = _scope_role_lines(guild_id, scope, bot, catalog=catalog, locale=locale)
    if roles:
        lines.append(_tr(catalog, "ui.tree.roles_heading", locale, "\U0001f465 R\u00f4les :"))
        lines.extend(f"\u2022 {item}" for item in roles)
    return "\n".join(lines)


def _scope_role_lines(
    guild_id: str, scope: str, bot: Any = None, catalog: Any = None, locale: str = "en"
) -> list[str]:
    """Return the scope's roles as bare mentions (the compact pattern).

    The platform declares the bot-admins role per guild; a mod's roles
    ride its own declaration and join the same notice.
    """
    def _t(key: str, fallback: str) -> str:
        if catalog is None:
            return fallback
        rendered = str(catalog.render(key, locale))
        return rendered if rendered != key else fallback

    return [f"<@&{role_id}>" for role_id in _bot_admins_role_ids(guild_id, bot)] or [
        "**bot-admins** ("
        + _t(
            "ui.tree.bot_admins_role_pending",
            "le r\u00f4le est cr\u00e9\u00e9 au premier sync",
        )
        + ")"
    ]


def _bot_admins_role_ids(guild_id: str, bot: Any = None) -> list[str]:
    """Resolve the guild's bot-admins role ids (by name, live guild)."""
    guild = getattr(bot, "get_guild", lambda _g: None)(int(guild_id)) if bot and guild_id.isdigit() else None
    if guild is None:
        return []
    return [str(r.id) for r in getattr(guild, "roles", ()) if r.name == "bot-admins"]


async def _mod_channels_lines(guild_id: str, name: str, definition: Any, catalog: Any, locale: str) -> list[str]:
    """Render the mod's own channels: its declaration is the source of truth.

    The notice READS the declaration (key, display_name, description)
    and the stored resolutions under the ``mod/<name>`` scope — a mod's
    home never lists the platform's mods folder (the 2026-11-13
    merlin report).
    """
    from botforge.discord.ui.panels_provisioning import stored_channel_id

    items: list[str] = []
    for channel in getattr(definition, "channel_categories", ()) or ():
        try:
            channel_id = await stored_channel_id(guild_id, f"mod/{name}", channel.key)
        except Exception:
            channel_id = None
        label = str(getattr(channel, "display_name", "") or channel.key)
        if channel_id:
            items.append(f"<#{channel_id}>")
        else:
            items.append(f"**{label}** (\u00e0 cr\u00e9er)")
    return items


async def _mod_roles_lines(guild_id: str, name: str, definition: Any, bot: Any, catalog: Any, locale: str) -> list[str]:
    """Render the mod's declared roles as bare mentions, plus the bot admins.

    The pattern everywhere (owner direction 2026-11-13): one compact
    line, the declared roles first, the bot-admins role closing it.
    """
    def _t(key: str, fallback: str) -> str:
        if catalog is None:
            return fallback
        rendered = str(catalog.render(key, locale))
        return rendered if rendered != key else fallback

    items: list[str] = []
    service = getattr(bot, "mod_roles_service", None)
    for role in getattr(definition, "roles", ()) or ():
        role_id = ""
        if service is not None:
            try:
                # the LEDGER is the only resolver (owner rule 2026-11-13:
                # never resolve a role or channel by name) — a role that
                # exists on Discord but lost its mapping renders as the
                # honest "à créer" until the repair re-provisions it
                mapping = await service.find_role_mapping(guild_id, name, role.key)
                role_id = str(mapping.role_id) if mapping is not None else ""
            except Exception:
                # never a silent "à créer": a broken ledger read is a
                # defect to see, not a state to render
                logger.warning(
                    "mod role resolution failed (%s:%s) — best-effort", name, role.key, exc_info=True
                )
                role_id = ""
        if role_id:
            items.append(f"<@&{role_id}>")
        else:
            items.append(f"**{getattr(role, 'display_name', role.key)}** (\u00e0 cr\u00e9er)")
    items.extend(f"<@&{role_id}>" for role_id in _bot_admins_role_ids(guild_id, bot))
    return items


async def _mod_notice(guild_id: str, name: str, catalog: Any, locale: str, bot: Any) -> str:
    """Render the mod's OWN notice: its declared channels and roles (its yaml)."""
    registry = getattr(bot, "registry", None)
    definition = registry.get(name) if registry is not None else None
    if definition is None:
        return ""
    lines: list[str] = []
    channels = await _mod_channels_lines(guild_id, name, definition, catalog, locale)
    if channels:
        lines.append(_tr(catalog, "ui.tree.channels_heading", locale, "\U0001f4fa Salons :"))
        lines.extend(f"\u2022 {item}" for item in channels)
    roles = await _mod_roles_lines(guild_id, name, definition, bot, catalog, locale)
    if roles:
        lines.append(_tr(catalog, "ui.tree.roles_heading", locale, "\U0001f465 R\u00f4les :"))
        lines.extend(f"\u2022 {item}" for item in roles)
    return "\n".join(lines)


async def _game_scope_channel_lines(
    guild_id: str, key: str, spec: Any, catalog: Any, locale: str
) -> list[str]:
    """Render one installed game's tree channels as mention lines."""
    from botforge.discord.ui.panels_provisioning import stored_channel_id

    lines: list[str] = []
    for decl in spec.channels:
        try:
            channel_id = await stored_channel_id(guild_id, f"games/{key}", decl.key)
        except Exception:
            channel_id = None
        if not channel_id:
            continue
        lines.append(f"<#{channel_id}>")
    return lines


async def _installed_scan_notice(
    guild_id: str, kind: str, catalog: Any, locale: str, bot: Any
) -> str:
    """Render the LIST views' notice: the installed games/mods' channels.

    The games/mods home and admin lists never show a hardcoded scope
    folder: they scan the guild's INSTALLED items and render each one's
    channels (a game's from its tree scope, a mod's from its yaml
    declaration) \u2014 the notice follows the live state.
    """
    from botforge.discord.ui.panels import registered_scopes
    from botforge.discord.wiring import build_guild_access_service

    access = build_guild_access_service()
    installed: tuple[str, ...] = ()
    if access is None:
        return ""
    try:
        installed = tuple(
            await (access.enabled_mods(guild_id) if kind == "mods" else access.enabled_games(guild_id))
        )
    except Exception:
        installed = ()
    channels: list[str] = []
    specs = {sp.scope: sp for sp in registered_scopes()}
    for key in installed:
        if kind == "mods":
            registry = getattr(bot, "registry", None)
            definition = registry.get(key) if registry is not None else None
            if definition is None:
                continue
            channels.extend(await _mod_channels_lines(guild_id, key, definition, catalog, locale))
        else:
            spec = specs.get(f"games/{key}")
            if spec is None:
                continue
            channels.extend(await _game_scope_channel_lines(guild_id, key, spec, catalog, locale))
    lines: list[str] = []
    if channels:
        lines.append(_tr(catalog, "ui.tree.channels_heading", locale, "\U0001f4fa Salons :"))
        lines.extend(f"\u2022 {item}" for item in channels)
    roles = [f"<@&{rid}>" for rid in _bot_admins_role_ids(guild_id, bot)]
    if roles:
        lines.append(_tr(catalog, "ui.tree.roles_heading", locale, "\U0001f465 R\u00f4les :"))
        lines.extend(f"\u2022 {item}" for item in roles)
    return "\n".join(lines)


def _declared_channel_meta(scope: str) -> dict[str, dict[str, Any]]:
    """Return the scope's channel declarations' raw metadata (descriptions)."""
    from botforge.discord.ui.panels_scopes import load_scopes_config

    raw = load_scopes_config().get(scope, {})
    return {str(c.get("key")): c for c in raw.get("channels", []) or []}



def _scope_channel(scope: str, key: str) -> Any:
    """Build a resolver for one tree channel (stored id only)."""

    async def _resolve(guild_id: str) -> str | None:
        from botforge.discord.ui.panels_provisioning import stored_channel_id

        return await stored_channel_id(guild_id, scope, key)

    return _resolve


# --- the per-scope home menus (the redesigned charte) ----------------


def register_scope_pins(bot: Any, scopes: tuple[str, ...]) -> list[Any]:
    """Declare (and register) every scope's home and admin pins.

    Idempotent: re-registering returns the already-registered specs
    (the static-pin store keeps the first per key).
    """
    from botforge.discord.static_pins import StaticPinnedView, register_static_pin

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
    from botforge.discord.views_routing import build_declared_menu

    inner = build_declared_menu(bot, view_id)

    async def _resolve_and_build(guild_id: str) -> Any:
        return await inner(guild_id)

    return _resolve_and_build


def register_mod_pins(bot: Any) -> list[Any]:
    """Declare every enabled mod's home and admin pins (idempotent).

    A mod's home menu pins in its declared "general" channel, its admin
    menu in its declared "admin" channel (the yaml's channel keys; a
    mod without them falls back to its first/last declared channel).
    The pins follow the same static-pin cycle as the platform scopes:
    created when missing, refreshed in place, healed on channel delete.
    """
    from botforge.core.ids import scope_view_key
    from botforge.discord.static_pins import StaticPinnedView, register_static_pin

    registry = getattr(bot, "registry", None)
    if registry is None:
        return []
    specs: list[Any] = []
    message_registry = getattr(bot, "message_registry", None)
    from botforge.discord.views_registry import get_view

    for name in registry.enabled():
        definition = registry.get(name)
        keys = [c.key for c in getattr(definition, "channel_categories", ()) or ()]
        if not keys:
            continue
        if get_view(f"menu:mod:{name}") is None:
            continue
        home_key = "general" if "general" in keys else keys[0]
        admin_key = "admin" if "admin" in keys else next((k for k in reversed(keys) if k != home_key), None)
        scope = f"mod/{name}"
        home_pin = scope_view_key(scope, "home")
        specs.append(
            register_static_pin(
                StaticPinnedView(
                    key=home_pin,
                    mark_suffix=home_pin,
                    resolve_channel=_scope_channel(scope, home_key),
                    build_layout=_declared_menu_builder(bot, f"menu:mod:{name}"),
                    registry=message_registry,
                    scope=scope,
                )
            )
        )
        if admin_key is None:
            continue
        admin_pin = scope_view_key(scope, "admin")
        specs.append(
            register_static_pin(
                StaticPinnedView(
                    key=admin_pin,
                    mark_suffix=admin_pin,
                    resolve_channel=_scope_channel(scope, admin_key),
                    build_layout=_declared_menu_builder(bot, f"menu:mod:{name}:admin"),
                    registry=message_registry,
                    scope=scope,
                )
            )
        )
    return specs


def _mod_pin_specs(bot: Any, name: str) -> list[Any]:
    """Return one mod's registered home/admin pin specs (empty when none)."""
    from botforge.core.ids import scope_view_key
    from botforge.discord.static_pins import registered_static_pins

    scope = f"mod/{name}"
    wanted = {scope_view_key(scope, "home"), scope_view_key(scope, "admin")}
    return [spec for spec in registered_static_pins() if spec.key in wanted]


async def ensure_mod_pins(bot: Any, guild_id: str, name: str) -> int:
    """(Re-)pin one mod's home and admin menus (install/repair path).

    The SDK owns the mod's surface lifecycle: the declaration carries
    the channels, the boot registers the specs, and the install/repair
    lands the pins right away \u2014 a mod dev never wires a pin.
    """
    import logging

    from botforge.discord.static_pins import ensure_static_pin

    logger = logging.getLogger("botforge.scope_pins")
    register_mod_pins(bot)
    landed = 0
    for spec in _mod_pin_specs(bot, name):
        try:
            await ensure_static_pin(bot, spec, guild_id)
            landed += 1
        except Exception:
            logger.warning("mod pin ensure failed (%s) \u2014 best-effort", spec.key, exc_info=True)
    return landed


async def remove_mod_pins(bot: Any, guild_id: str, name: str) -> int:
    """Remove one mod's pins (uninstall path) \u2014 whatever the channel choice.

    The uninstalled mod's menus must not stay pinned (nor be healed
    back by the ensure cycle): the messages go, the registry forgets
    the ids, the specs drop.
    """
    import logging

    from botforge.discord.static_pins import remove_static_pin, unregister_static_pin

    logger = logging.getLogger("botforge.scope_pins")
    removed = 0
    for spec in _mod_pin_specs(bot, name):
        try:
            await remove_static_pin(bot, spec, guild_id)
            removed += 1
        except Exception:
            logger.warning("mod pin removal failed (%s) \u2014 best-effort", spec.key, exc_info=True)
        unregister_static_pin(spec.key)
    return removed


__all__ = ["ensure_mod_pins", "register_mod_pins", "register_scope_pins", "remove_mod_pins"]
