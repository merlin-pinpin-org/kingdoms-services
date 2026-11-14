"""The install/uninstall catalogs: one post per game, one per mod (botforge-services#374).

TWO forums, two perimeters, the same logic: the games catalog (the
``games`` scope's ``catalog`` channel) carries one post per declared
game; the mods catalog (the ``mods`` scope's ``catalog`` channel)
carries one post per declared mod. The posts are built from the
declarations and carry their state (installed or not) and the actions:

- **Install**: the item joins the guild's active set (the required
  games ride along — the service's rules).
- **Uninstall**: a modal with the data choice — keep the mod's data
  (a reinstall restores the state) or delete it (the ``<mod>:``
  namespace wipe). A game an active mod requires is refused with the
  dependents named.

The posts are idempotent: the sync (boot + the mods Synchroniser)
creates the missing ones and refreshes the existing ones — stored
thread ids under ``mod_catalog_posts``, the same resolution contract
as the maps forum. The norm: the post view carries its id footer, the
actions are guarded at click time (bot admin), every string renders
in the guild's locale.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import discord

logger = logging.getLogger("botforge.discord.mods_forum")

CATALOG_POSTS_COLLECTION = "mod_catalog_posts"
CATALOG_CHANNEL_KEY = "catalog"

# one catalog per perimeter: the kind decides the tree scope and the items
CATALOG_SCOPES: dict[str, str] = {"game": "games", "mod": "mods"}

_NS = "modcatalog"

_INSTALL_TEMPLATE = rf"{_NS}:install:(?P<kind>game|mod):(?P<name>[a-z0-9_-]+)"
_UNINSTALL_TEMPLATE = rf"{_NS}:uninstall:(?P<kind>game|mod):(?P<name>[a-z0-9_-]+)"
_ADMIN_TEMPLATE = rf"{_NS}:admin:(?P<kind>game|mod):(?P<name>[a-z0-9_-]+)"


def load_game_declarations(config_dir: Any) -> dict[str, dict[str, Any]]:
    """Load every game's declaration (config/games/<key>/game.yaml).

    A missing declaration is the minimal form (default_active: true);
    the presentation keys (display_key, providers) are optional.
    """
    from pathlib import Path

    import yaml as _yaml

    games_dir = Path(config_dir) / "games"
    out: dict[str, dict[str, Any]] = {}
    if not games_dir.is_dir():
        return out
    for entry in sorted(games_dir.iterdir()):
        if not entry.is_dir():
            continue
        decl_path = entry / "game.yaml"
        data: dict[str, Any] = {"id": entry.name, "default_active": True}
        if decl_path.is_file():
            with open(decl_path, encoding="utf-8") as fh:
                loaded = _yaml.safe_load(fh) or {}
            if isinstance(loaded, dict):
                data.update(loaded)
        out[entry.name] = data
    return out


def _game_declaration(bot: Any, game_key: str) -> dict[str, Any]:
    """Return one game's declaration (empty when unconfigured)."""
    config_dir = getattr(getattr(bot, "config", None), "config_dir", None) or "config"
    return load_game_declarations(config_dir).get(game_key, {})


def _catalog_post_id(guild_id: str, kind: str, name: str) -> str:
    """Return the bookkeeping document id for one catalog post."""
    return f"{guild_id}:{kind}:{name}"


async def _catalog_forum_id(guild_id: str, kind: str) -> str | None:
    """Resolve one catalog forum's stored id (the provisioning owns creation)."""
    from botforge.discord.ui.panels_provisioning import stored_channel_id

    stored = await stored_channel_id(guild_id, CATALOG_SCOPES[kind], CATALOG_CHANNEL_KEY)
    return str(stored) if stored and str(stored).isdigit() else None


def _build_service(bot: Any) -> Any | None:
    """Build the install service over the wired seams; None unwired."""
    from botforge.core.services.mod_install import ModInstallService
    from botforge.discord.wiring import build_guild_access_service

    access = build_guild_access_service()
    registry = getattr(bot, "registry", None)
    if access is None or registry is None:
        return None
    try:
        from botforge.core.models.db import get_async_database

        database = get_async_database()
    except Exception:
        database = None
    return ModInstallService(access, registry, database=database)


async def _tr(bot: Any, guild_id: str, key: str, fallback: str) -> str:
    """Render one catalog string in the guild's locale (en fallback)."""
    from botforge.discord.scope_pins import _catalog, _locale_of, _tr

    return _tr(_catalog(bot), f"mods.forum.{key}", await _locale_of(bot, guild_id), fallback)


def _catalog_post_view(
    kind: str,
    name: str,
    installed: bool,
    required_games: tuple[str, ...] = (),
    labels: dict[str, str] | None = None,
    providers: tuple[tuple[str, str], ...] = (),
    links: dict[str, str] | None = None,
) -> discord.ui.LayoutView:
    """Build one catalog post: the identity, the state, the actions, the id footer.

    A GAME post carries its long display name and its providers (what
    each data source is for — the catalog is a showcase, not a bare
    key); a MOD post carries its required games.
    """
    labels = labels or {}
    links = links or {}
    view = discord.ui.LayoutView(timeout=None)
    status = labels.get("installed" if installed else "not_installed", "")
    blocks: list[Any] = [
        discord.ui.TextDisplay(f"# {labels.get('title', name)}"),
        discord.ui.TextDisplay(status),
    ]
    if providers:
        lines = "\n".join(f"• **{key}** — {purpose}" for key, purpose in providers)
        blocks.append(discord.ui.TextDisplay(lines))
    if required_games:
        # every required item links to ITS catalog post when it exists
        # (a click opens the dependency — the catalog is a web, not a list)
        rendered = [links.get(item, item) for item in required_games]
        blocks.append(discord.ui.TextDisplay(f"{labels.get('requires', 'Requires')}: {', '.join(rendered)}"))
    view.add_item(discord.ui.Container(*blocks, accent_colour=discord.Colour(0x5865F2)))
    # the install/uninstall actions hide behind the single ⚙️ Admin button
    # (the maps/pools precedent): the click is guarded, the actions ride
    # an EPHEMERAL admin-only view — the public post never carries them
    row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    row.add_item(
        ModAdminButton(kind, name, label=labels.get("admin_btn", "Admin"))
    )
    view.add_item(row)
    from botforge.discord.ui.view_footer import add_view_footer

    return add_view_footer(view, f"panel:{kind}:{name}")


async def _stored_post_ids(guild_id: str) -> dict[str, str]:
    """Return the stored thread ids of a guild's catalog posts (id -> thread)."""
    from botforge.core.models.db import get_async_database

    database = get_async_database()
    out: dict[str, str] = {}
    async for doc in database[CATALOG_POSTS_COLLECTION].find({"guild_id": guild_id}):
        out[str(doc["_id"])] = str(doc.get("thread_id") or "")
    return out


async def _create_post(
    bot: Any,
    guild_id: str,
    forum_id: str,
    kind: str,
    name: str,
    view: discord.ui.LayoutView,
    title: str = "",
) -> str | None:
    """Create one catalog post in ITS perimeter's forum (game→games, mod→mods).

    The THREAD's name is the item's display name (the long title), not
    its key — the forum reads like a showcase.
    """
    """Create one catalog post; return its thread id (None on failure)."""
    guild = bot.get_guild(int(guild_id))
    forum = guild.get_channel(int(forum_id)) if guild is not None else None
    if guild is None or forum is None:
        return None
    if not isinstance(forum, discord.ForumChannel):
        logger.warning(
            "mods forum: the stored catalog channel %s is a %s, not a forum — "
            "re-run the Synchroniser (the provisioning recreates it)",
            forum_id,
            type(forum).__name__,
        )
        return None
    try:
        # create_thread returns (thread, message): the unpack is the
        # contract (the merlin AttributeError — thread.id on the tuple)
        thread, _message = await forum.create_thread(
            name=(title or name)[:90],
            reason=f"botforge: mods catalog post {name}",
            silent=True,
            view=view,
        )
    except discord.HTTPException:
        logger.warning("mods forum: post creation failed for %s %s — best-effort", kind, name, exc_info=True)
        return None
    from botforge.core.models.db import get_async_database

    post_id = _catalog_post_id(guild_id, kind, name)
    thread_id = str(thread.id)
    await get_async_database()[CATALOG_POSTS_COLLECTION].update_one(
        {"_id": post_id},
        {"$set": {"guild_id": guild_id, "kind": kind, "name": name, "thread_id": thread_id}},
        upsert=True,
    )
    return thread_id


async def _refresh_post(
    bot: Any,
    guild_id: str,
    thread_id: str,
    view: discord.ui.LayoutView,
    title: str = "",
) -> bool:
    """Edit one catalog post's starter message; False when the thread is gone."""
    guild = bot.get_guild(int(guild_id))
    thread = guild.get_thread(int(thread_id)) if guild is not None else None
    if thread is None:
        return False
    try:
        if title and thread.name != (title or thread.name)[:90]:
            await thread.edit(name=title[:90], reason="botforge: mods catalog (title refresh)")
        message = await thread.fetch_message(thread.id)
        await message.edit(view=view)
    except discord.HTTPException:
        logger.warning("mods forum: post refresh failed (thread %s) — best-effort", thread_id, exc_info=True)
        return False
    return True


async def _item_title(bot: Any, guild_id: str, kind: str, name: str) -> str:
    """Return one item's display title (the thread's name, the post's heading)."""
    if kind == "game":
        title, _providers = await _game_presentation(bot, guild_id, name)
        return title
    from botforge.discord.scope_pins import _catalog, _locale_of
    from botforge.discord.scope_pins import _tr as _tr_key

    return _tr_key(_catalog(bot), f"ui.mods.homes.{name}", await _locale_of(bot, guild_id), name)


async def _sync_one_item(
    bot: Any,
    guild_id: str,
    kind: str,
    name: str,
    installed: bool,
    stored: dict[str, str],
    registry: Any,
) -> discord.ui.LayoutView | None:
    """Build ONE item's refreshed post view (None when it cannot render)."""
    required: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    providers: tuple[tuple[str, str], ...] = ()
    title = name
    if kind == "mod" and registry is not None:
        definition = registry.get(name)
        if definition is not None:
            required = tuple(getattr(definition, "required_games", ()) or ())
            dependencies = tuple(getattr(definition, "dependencies", ()) or ())
        from botforge.discord.scope_pins import _catalog, _locale_of
        from botforge.discord.scope_pins import _tr as _tr_key

        title = _tr_key(_catalog(bot), f"ui.mods.homes.{name}", await _locale_of(bot, guild_id), name)
    if kind == "game":
        title, providers = await _game_presentation(bot, guild_id, name)
    labels = {
        "title": title,
        "installed": await _tr(bot, guild_id, "installed", "Installed"),
        "not_installed": await _tr(bot, guild_id, "not_installed", "Not installed"),
        "requires": await _tr(bot, guild_id, "requires", "Requires"),
        "install": await _tr(bot, guild_id, "install_btn", "Install"),
        "uninstall": await _tr(bot, guild_id, "uninstall_btn", "Uninstall"),
    }
    # every required game AND every required mod links to its catalog
    # post (the stored threads): a click opens the dependency
    links: dict[str, str] = {}
    for item in required:
        post_id = _catalog_post_id(guild_id, "game", item)
        if post_id in stored:
            links[item] = f"<#{stored[post_id]}>"
    for item in dependencies:
        post_id = _catalog_post_id(guild_id, "mod", item)
        if post_id in stored:
            links[item] = f"<#{stored[post_id]}>"
    required_line = tuple(required) + tuple(d for d in dependencies if d not in required)
    return _catalog_post_view(kind, name, installed, required_line, labels, providers, links)


async def _sync_one_catalog(guild_id: str, bot: Any, kind: str) -> int:
    """Sync ONE catalog: its forum, its items, refreshed in place."""
    service = _build_service(bot)
    if service is None:
        return 0
    forum_id = await _catalog_forum_id(guild_id, kind)
    if forum_id is None:
        return 0  # the provisioning owns creation; nothing stored yet
    from botforge.discord.wiring import guild_access_platform

    all_games, all_mods = guild_access_platform()
    items = all_games if kind == "game" else all_mods
    status = await service.status(guild_id)
    installed_set = set(status.get(f"{kind}s") or ())
    registry = getattr(bot, "registry", None)

    async def _labels(installed: bool, title: str) -> dict[str, str]:
        return {
            "title": title,
            "installed": await _tr(bot, guild_id, "installed", "Installed"),
            "not_installed": await _tr(bot, guild_id, "not_installed", "Not installed"),
            "requires": await _tr(bot, guild_id, "requires", "Requires"),
            "install": await _tr(bot, guild_id, "install_btn", "Install"),
            "uninstall": await _tr(bot, guild_id, "uninstall_btn", "Uninstall"),
        }

    stored = await _stored_post_ids(guild_id)
    changed = 0
    for name in items:
        view = await _sync_one_item(bot, guild_id, kind, name, name in installed_set, stored, registry)
        if view is None:
            continue
        title = await _item_title(bot, guild_id, kind, name)
        post_id = _catalog_post_id(guild_id, kind, name)
        thread_id = stored.get(post_id, "")
        if thread_id and await _refresh_post(bot, guild_id, thread_id, view, title):
            changed += 1
            continue
        if await _create_post(bot, guild_id, forum_id, kind, name, view, title):
            changed += 1
    return changed


async def sync_catalogs(guild_id: str, bot: Any) -> int:
    """Sync both catalogs: the games' and the mods' — two perimeters, one pass."""
    return await _sync_one_catalog(guild_id, bot, "game") + await _sync_one_catalog(guild_id, bot, "mod")


async def _guard(interaction: discord.Interaction) -> bool:
    """Guard the catalog actions: bot-admin only, checked at click time."""
    from botforge.discord.guards import require_admin

    bot = interaction.client
    admins = tuple(getattr(getattr(bot, "status_service", None), "bot_admins", ()) or ())
    denied = await _tr(
        bot,
        str(interaction.guild_id or ""),
        "admin_only",
        "The catalog actions are reserved for bot admins.",
    )
    return await require_admin(
        interaction, admins, getattr(bot, "roles_service", None), denied_message=denied
    )


async def provision_game_scope(bot: Any, guild_id: str, game_key: str) -> int:
    """Provision one game's scope folder (installing a game creates its channels).

    The same core calls the Synchroniser runs (provision_scope +
    persist_resolutions); returns the channels ensured.
    """
    from botforge.core.services.i18n import DEFAULT_LOCALE
    from botforge.discord.ui.panels import registered_scopes
    from botforge.discord.ui.panels_provisioning import persist_resolutions, provision_scope

    guild = bot.get_guild(int(guild_id)) if guild_id.isdigit() else None
    if guild is None:
        return 0
    scope = f"games/{game_key}"
    spec = next((s for s in registered_scopes() if s.scope == scope), None)
    if spec is None:
        return 0
    catalog = getattr(bot, "messages", None)
    locale = DEFAULT_LOCALE
    logs = getattr(bot, "logs_service", None)
    if logs is not None:
        try:
            locale = str(await logs.get_locale(guild_id) or DEFAULT_LOCALE)
        except Exception:
            locale = DEFAULT_LOCALE

    def _translate(key: str, _locale: str) -> str:
        if catalog is None:
            return key
        rendered: str = catalog.render(key, _locale)
        return rendered if rendered != key else catalog.render(key, "en")

    try:
        resolved = await provision_scope(guild, spec, locale, _translate)
        await persist_resolutions(guild_id, scope, resolved)
        return len(resolved)
    except Exception:
        logger.warning("catalog: provisioning the %s scope failed — best-effort", scope, exc_info=True)
        return 0


async def delete_game_scope(bot: Any, guild_id: str, game_key: str) -> int:
    """Delete one game's provisioned channels (uninstall with the choice).

    Every stored resolution under ``tree:games/<key>:`` goes: the
    channels are deleted on Discord, the stored ids with them. The
    game's DATA is untouched (the data choice is separate).
    """
    guild = bot.get_guild(int(guild_id)) if guild_id.isdigit() else None
    if guild is None:
        return 0
    from botforge.core.models.db import get_async_database

    database = get_async_database()
    prefix = f"{guild_id}:tree:games/{game_key}:"
    deleted = 0
    async for doc in database["channels"].find({"_id": {"$regex": f"^{prefix}"}}):
        channel_id = str(doc.get("channel_id") or "")
        if channel_id and channel_id.isdigit():
            channel = guild.get_channel(int(channel_id))
            if channel is not None:
                try:
                    await channel.delete(reason=f"botforge: uninstall the {game_key} game")
                    deleted += 1
                except discord.HTTPException:
                    logger.warning(
                        "catalog: deleting the %s channel failed — best-effort", channel_id, exc_info=True
                    )
    await database["channels"].delete_many({"_id": {"$regex": f"^{prefix}"}})
    return deleted


async def _game_presentation(
    bot: Any, guild_id: str, game_key: str
) -> tuple[str, tuple[tuple[str, str], ...]]:
    """Return the game's long title and its (provider, purpose) pairs.

    The declaration (config/games/<key>/game.yaml) carries the display
    key and the providers' purpose keys; the locale renders them.
    """
    from botforge.discord.scope_pins import _catalog, _locale_of
    from botforge.discord.scope_pins import _tr as _tr_key

    decl = _game_declaration(bot, game_key)
    catalog = _catalog(bot)
    locale = await _locale_of(bot, guild_id)
    title = _tr_key(catalog, str(decl.get("display_key", "")), locale, game_key)
    providers: tuple[tuple[str, str], ...] = ()
    raw_providers = decl.get("providers") or ()
    if isinstance(raw_providers, list):
        pairs = []
        for entry in raw_providers:
            if isinstance(entry, dict) and entry.get("key"):
                purpose = _tr_key(
                    catalog, str(entry.get("purpose_key", "")), locale, str(entry.get("key"))
                )
                pairs.append((str(entry["key"]), purpose))
        providers = tuple(pairs)
    return title, providers


async def refresh_scope_pins(bot: Any, guild_id: str, scopes: tuple[str, ...]) -> int:
    """Re-render the pinned views of the given scopes (best-effort).

    The install/uninstall changes the guild's live state: the pinned
    home and admin menus carry it (the auto-injected entries' enabled
    and grayed buttons) — a stale pin shows a stale state until the
    next sync. The refresh keeps them honest.
    """
    from botforge.discord.static_pins import ensure_static_pin, registered_static_pins

    refreshed = 0
    for pin in registered_static_pins():
        if not pin.scope or not any(pin.scope == s or pin.scope.startswith(s + "/") for s in scopes):
            continue
        try:
            await ensure_static_pin(bot, pin, guild_id)
            refreshed += 1
        except Exception:
            logger.warning("catalog: the %s pin refresh failed — best-effort", pin.key, exc_info=True)
    return refreshed


async def provision_mod_channels(bot: Any, guild_id: str, mod_name: str) -> int:
    """Provision one mod's declared channels under its own folder (mod/<name>).

    The declaration's channels ride the SAME provisioning machinery as
    the tree scopes (provision_scope + persist_resolutions): the stored
    ids live under the ``mod/<name>`` scope, the heal and the repair
    button work exactly like the games'.
    """
    from botforge.discord.ui.panels import ChannelDecl, ScopeSpec
    from botforge.discord.ui.panels_provisioning import persist_resolutions, provision_scope

    guild = bot.get_guild(int(guild_id)) if guild_id.isdigit() else None
    registry = getattr(bot, "registry", None)
    definition = registry.get(mod_name) if registry is not None else None
    if guild is None or definition is None:
        return 0
    declared = tuple(getattr(definition, "channel_categories", ()) or ())
    if not declared:
        return 0
    from botforge.discord.scope_pins import _catalog, _locale_of
    from botforge.discord.scope_pins import _tr as _tr_key

    catalog = _catalog(bot)
    locale = await _locale_of(bot, guild_id)
    category_name = _tr_key(catalog, f"ui.mods.homes.{mod_name}", locale, mod_name)
    spec = ScopeSpec(
        scope=f"mod/{mod_name}",
        category_key=category_name,
        channels=tuple(
            ChannelDecl(
                key=channel.key,
                label_key=channel.display_name,
                kind="text",
                access="admin" if getattr(channel, "admin_only", False) else "public",
                group=str(getattr(channel, "group", "") or ""),
                policy=getattr(channel, "access", None),
            )
            for channel in declared
        ),
    )

    def _translate(key: str, _locale: str) -> str:
        rendered: str = catalog.render(key, _locale)
        return rendered if rendered != key else catalog.render(key, "en")

    try:
        resolved = await provision_scope(guild, spec, locale, _translate)
        await persist_resolutions(guild_id, spec.scope, resolved)
        return len(resolved)
    except Exception:
        logger.warning(
            "catalog: provisioning the %s mod channels failed — best-effort", mod_name, exc_info=True
        )
        return 0


async def delete_mod_scope(bot: Any, guild_id: str, mod_name: str) -> int:
    """Delete one mod's provisioned channels and folder (uninstall with the choice).

    Every stored resolution under ``tree:mod/<name>:`` goes (the folder
    and its channels); the mod's DATA is untouched (the data choice is
    separate).
    """
    guild = bot.get_guild(int(guild_id)) if guild_id.isdigit() else None
    if guild is None:
        return 0
    from botforge.core.models.db import get_async_database

    database = get_async_database()
    prefix = f"{guild_id}:tree:mod/{mod_name}:"
    deleted = 0
    async for doc in database["channels"].find({"_id": {"$regex": f"^{prefix}"}}):
        channel_id = str(doc.get("channel_id") or "")
        if channel_id and channel_id.isdigit():
            channel = guild.get_channel(int(channel_id))
            if channel is not None:
                try:
                    await channel.delete(reason=f"botforge: uninstall the {mod_name} mod")
                    deleted += 1
                except discord.HTTPException:
                    logger.warning(
                        "catalog: deleting the %s channel failed — best-effort", channel_id, exc_info=True
                    )
    await database["channels"].delete_many({"_id": {"$regex": f"^{prefix}"}})
    return deleted


async def provision_mod_roles(bot: Any, guild_id: str, mod_name: str) -> int:
    """Provision one mod's declared roles (installing a mod creates them)."""
    mod_roles_service = getattr(bot, "mod_roles_service", None)
    if mod_roles_service is None:
        return 0
    try:
        resolved = await mod_roles_service.setup_mod_roles(guild_id, mod_name)
        return len(resolved)
    except Exception:
        logger.warning(
            "catalog: provisioning the %s mod roles failed — best-effort", mod_name, exc_info=True
        )
        return 0


async def teardown_mod_roles(bot: Any, guild_id: str, mod_name: str) -> int:
    """Delete one mod's provisioned roles (uninstall with the choice)."""
    mod_roles_service = getattr(bot, "mod_roles_service", None)
    if mod_roles_service is None:
        return 0
    try:
        return int(await mod_roles_service.teardown_mod_roles(guild_id, mod_name))
    except Exception:
        logger.warning(
            "catalog: tearing down the %s mod roles failed — best-effort", mod_name, exc_info=True
        )
        return 0


async def _action_view(bot: Any, guild_id: str, kind: str, name: str) -> discord.ui.View:
    """Build the item's ACTION view (ephemeral, admin-only).

    The button reflects the CURRENT state — Install when not installed,
    Uninstall when installed.
    """
    service = _build_service(bot)
    status = await service.status(guild_id) if service is not None else {}
    installed = name in set(status.get(f"{kind}s") or ())
    if installed:
        label = await _tr(bot, guild_id, "uninstall_btn", "Uninstall")
        return _button_view(f"{_NS}:uninstall:{kind}:{name}", label, discord.ButtonStyle.danger)
    label = await _tr(bot, guild_id, "install_btn", "Install")
    return _button_view(f"{_NS}:install:{kind}:{name}", label, discord.ButtonStyle.success)


def _button_view(custom_id: str, label: str, style: discord.ButtonStyle) -> discord.ui.View:
    """Build a one-button view (the ephemeral action bar)."""
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label=label[:80], style=style, custom_id=custom_id[:100]))
    return view


class ModRepairButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:repair:(?P<name>[a-z0-9_-]+)",
):
    """The mod admin menu's repair button: re-provisions the mod's folder.

    The declared channels AND roles (the same pass the install runs) —
    the label says what it does (botforge-services#384).
    """

    def __init__(self, name: str, label: str = "Repair channels & roles") -> None:
        from botforge.discord.ui.emoji import EMOJI

        self.name = name
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                emoji=EMOJI.WRENCH,
                style=discord.ButtonStyle.secondary,
                custom_id=f"{_NS}:repair:{name}"[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> ModRepairButton:
        """Rebuild the button from the wire."""
        del interaction, item
        return cls(match["name"])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Repair the mod's channels and roles (guarded, best-effort)."""
        if not await _guard(interaction):
            return
        # the provisioning can exceed Discord's 3s window: acknowledge first
        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild_id or "")
        channels = await provision_mod_channels(interaction.client, guild_id, self.name)
        roles = await provision_mod_roles(interaction.client, guild_id, self.name)
        from botforge.discord.scope_pins import ensure_mod_pins

        await ensure_mod_pins(interaction.client, guild_id, self.name)
        message = (await _tr(interaction.client, guild_id, "repaired", "Repaired.")).format(
            channels=channels, roles=roles
        )
        await interaction.followup.send(message, ephemeral=True)


class ModAdminButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=re.compile(_ADMIN_TEMPLATE),
):
    """The single ⚙️ Admin button of a catalog post (persistent).

    The click is guarded (bot admin); the install/uninstall actions
    ride an EPHEMERAL view — the maps/pools precedent: the public post
    never carries the actions.
    """

    def __init__(self, kind: str, name: str, label: str = "Admin") -> None:
        self.kind = kind
        self.name = name
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                emoji="\U0001f527",
                style=discord.ButtonStyle.secondary,
                custom_id=f"{_NS}:admin:{kind}:{name}"[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> ModAdminButton:
        """Rebuild the button from the wire."""
        del interaction, item
        return cls(match["kind"], match["name"])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Guard, then send the item's action view ephemeral, admin-only."""
        if not await _guard(interaction):
            return
        service = _build_service(interaction.client)
        if service is None:
            return
        guild_id = str(interaction.guild_id or "")
        view = await _action_view(interaction.client, guild_id, self.kind, self.name)
        await interaction.response.send_message(view=view, ephemeral=True)


class ModInstallButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=re.compile(_INSTALL_TEMPLATE),
):
    """One catalog post's Install button (guarded at click time)."""

    def __init__(self, kind: str, name: str, label: str = "Install") -> None:
        self.kind = kind
        self.name = name
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                style=discord.ButtonStyle.success,
                custom_id=f"{_NS}:install:{kind}:{name}"[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> ModInstallButton:
        """Rebuild the item from the wire."""
        del interaction, item
        return cls(match["kind"], match["name"])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Install the item; the service's rules apply (deps ride along)."""
        if not await _guard(interaction):
            return
        # the provisioning can exceed Discord's 3s window: acknowledge
        # FIRST (defer), work after — the merlin timeout finding
        await interaction.response.defer(ephemeral=True)
        service = _build_service(interaction.client)
        if service is None:
            return
        kwargs: dict[str, tuple[str, ...]] = {f"{self.kind}s": (self.name,)}
        guild_id = str(interaction.guild_id or "")
        result = await service.install(guild_id, **kwargs)
        if not result.ok:
            await interaction.response.send_message(
                (
                await _tr(
                    interaction.client, guild_id, "refused",
                    "Refused: {reason}",
                )
            ).format(reason=result.summary),
                ephemeral=True,
            )
            return
        # installing a GAME creates its channels (the scope folder);
        # installing a MOD provisions its declared roles
        ensured = 0
        if self.kind == "game":
            ensured = await provision_game_scope(interaction.client, guild_id, self.name)
        if self.kind == "mod":
            ensured = await provision_mod_channels(interaction.client, guild_id, self.name)
            ensured += await provision_mod_roles(interaction.client, guild_id, self.name)
            from botforge.discord.scope_pins import ensure_mod_pins

            await ensure_mod_pins(interaction.client, guild_id, self.name)
        message = await _tr(interaction.client, guild_id, "installed_done", "Installed.")
        if ensured:
            key = "installed_game" if self.kind == "game" else "installed_mod"
            fallback = "{count} channels" if self.kind == "game" else "{count} roles"
            message += " " + (await _tr(interaction.client, guild_id, key, fallback)).format(
                count=ensured
            )
        # the ephemeral action bar REFRESHES in place: the button flips
        # to the item's new state (the confirmation rides the edit)
        try:
            if interaction.message is None:
                raise AttributeError("no message to edit")
            await interaction.followup.edit_message(
                interaction.message.id,
                content=message,
                view=await _action_view(interaction.client, guild_id, self.kind, self.name),
            )
        except (discord.HTTPException, AttributeError, TypeError):
            await interaction.followup.send(message, ephemeral=True)
        await sync_catalogs(str(interaction.guild_id), interaction.client)
        # the pinned home/admin menus re-render: the live install state
        # rides their auto-injected entries
        pin_scopes = ("games",) if self.kind == "game" else ("mods",)
        await refresh_scope_pins(interaction.client, str(interaction.guild_id), pin_scopes)


class ModUninstallButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=re.compile(_UNINSTALL_TEMPLATE),
):
    """One catalog post's Uninstall button — opens the data-choice modal."""

    def __init__(self, kind: str, name: str, label: str = "Uninstall") -> None:
        self.kind = kind
        self.name = name
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                style=discord.ButtonStyle.danger,
                custom_id=f"{_NS}:uninstall:{kind}:{name}"[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> ModUninstallButton:
        """Rebuild the item from the wire."""
        del interaction, item
        return cls(match["kind"], match["name"])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the uninstall modal (the data choice is explicit)."""
        if not await _guard(interaction):
            return
        await interaction.response.send_modal(
            await ModUninstallModal.build(interaction, self.kind, self.name)
        )


async def _apply_uninstall_choice(
    bot: Any, guild_id: str, kind: str, name: str, drop_channels: bool
) -> int:
    """Apply the uninstall's channels/roles choice; return what was deleted.

    The uninstalled mod's pins are removed whatever the choice: the
    pinned menus must not survive their mod.
    """
    deleted = 0
    if drop_channels:
        if kind == "game":
            deleted = await delete_game_scope(bot, guild_id, name)
        if kind == "mod":
            deleted = await delete_mod_scope(bot, guild_id, name)
            deleted += await teardown_mod_roles(bot, guild_id, name)
    if kind == "mod":
        from botforge.discord.scope_pins import remove_mod_pins

        await remove_mod_pins(bot, guild_id, name)
    return deleted


class ModUninstallModal(discord.ui.Modal):
    """The uninstall modal: the data AND the channels/roles choices — explicit, never a default."""

    KEEP, DELETE = "keep", "delete"

    @classmethod
    async def build(cls, interaction: discord.Interaction, kind: str, name: str) -> ModUninstallModal:
        """Build the modal with the guild's locale (en fallback)."""
        bot = interaction.client
        guild_id = str(interaction.guild_id or "")

        async def tr(key: str, fallback: str) -> str:
            return await _tr(bot, guild_id, key, fallback)

        return cls(
            kind=kind,
            name=name,
            title=(await tr("uninstall_modal_title", "Uninstall {item}")).format(item=name)[:45],
            data_label=(await tr("opt_data_label", "Data"))[:45],
            data_keep=await tr("opt_keep", "Keep the data (a reinstall restores the state)"),
            data_delete=await tr("opt_delete", "Delete the data (the mod's own collections)"),
            channels_label=(await tr("opt_channels_label", "Channels and roles"))[:45],
            channels_keep=await tr("opt_channels_keep", "Keep the channels and roles (a reinstall finds them)"),
            channels_delete=await tr("opt_channels_delete", "Delete the channels and roles of this item"),
            done=await tr("uninstalled_done", "Uninstalled."),
            refused=await tr("refused", "Refused: {reason}"),
        )

    def __init__(
        self,
        *,
        kind: str,
        name: str,
        title: str,
        data_label: str,
        data_keep: str,
        data_delete: str,
        channels_label: str,
        channels_keep: str,
        channels_delete: str,
        done: str,
        refused: str,
    ) -> None:
        super().__init__(title=title, timeout=600.0)
        self.kind = kind
        self.name = name
        self.done = done
        self.refused = refused
        self.data_select: discord.ui.Select[Any] = discord.ui.Select(
            placeholder=data_label[:100],
            min_values=0,
            max_values=1,
            required=False,
            options=[
                discord.SelectOption(label=data_keep[:100], value=self.KEEP),
                discord.SelectOption(label=data_delete[:100], value=self.DELETE),
            ],
        )
        self.add_item(discord.ui.Label(text=data_label, component=self.data_select))
        self.channels_select: discord.ui.Select[Any] = discord.ui.Select(
            placeholder=channels_label[:100],
            min_values=0,
            max_values=1,
            required=False,
            options=[
                discord.SelectOption(label=channels_keep[:100], value=self.KEEP),
                discord.SelectOption(label=channels_delete[:100], value=self.DELETE),
            ],
        )
        self.add_item(discord.ui.Label(text=channels_label, component=self.channels_select))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Apply the uninstall with the stated data and channels/roles choices."""
        if not await _guard(interaction):
            return
        # the teardown can exceed Discord's 3s window: acknowledge first
        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild_id or "")
        wipe = bool(self.data_select.values and self.data_select.values[0] == self.DELETE)
        drop_channels = bool(
            self.channels_select.values and self.channels_select.values[0] == self.DELETE
        )
        service = _build_service(interaction.client)
        if service is None:
            return
        kwargs: dict[str, Any] = {f"{self.kind}s": (self.name,), "wipe_data": wipe}
        result = await service.uninstall(guild_id, **kwargs)
        if not result.ok:
            # the refused reason names the DEPENDENTS (the mods that
            # require the item), rendered in the guild's locale
            from botforge.discord.scope_pins import _catalog, _locale_of
            from botforge.discord.scope_pins import _tr as _tr_key

            catalog = _catalog(interaction.client)
            locale = await _locale_of(interaction.client, guild_id)
            names = [
                _tr_key(catalog, f"ui.mods.homes.{item}", locale, item)
                for item in result.refused_by
            ] or [result.summary]
            await interaction.followup.send(
                self.refused.format(reason=", ".join(names)), ephemeral=True
            )
            return
        # the channels/roles choice: delete the item's provisioned scope;
        # an uninstalled MOD's pinned menus go with it — whatever the
        # channel choice (the pin must not survive its mod)
        deleted = await _apply_uninstall_choice(interaction.client, guild_id, self.kind, self.name, drop_channels)
        message = self.done
        if deleted:
            key = "uninstalled_game" if self.kind == "game" else "uninstalled_mod"
            fallback = "{count} channels deleted" if self.kind == "game" else "{count} roles deleted"
            message += " " + (await _tr(interaction.client, guild_id, key, fallback)).format(
                count=deleted
            )
        # the ephemeral action bar REFRESHES in place: the button flips
        # to the item's new state (the confirmation rides the edit)
        try:
            if interaction.message is None:
                raise AttributeError("no message to edit")
            await interaction.followup.edit_message(
                interaction.message.id,
                content=message,
                view=await _action_view(interaction.client, guild_id, self.kind, self.name),
            )
        except (discord.HTTPException, AttributeError, TypeError):
            await interaction.followup.send(message, ephemeral=True)
        await sync_catalogs(guild_id, interaction.client)
        pin_scopes = ("games",) if self.kind == "game" else ("mods",)
        await refresh_scope_pins(interaction.client, guild_id, pin_scopes)


def register_mods_forum_items(bot: discord.Client) -> None:
    """Register the catalog's DynamicItems (called at every startup)."""
    bot.add_dynamic_items(ModInstallButton, ModUninstallButton, ModAdminButton, ModRepairButton)


def register_catalog_syncs() -> None:
    """Register the catalogs' syncs per scope (the Synchroniser's contract).

    The games scope's Synchroniser re-syncs the games catalog, the mods
    scope's the mods catalog — the channels are provisioned first, the
    posts follow in the same pass (the boot alone cannot: the channels
    do not exist yet on a virgin guild).
    """
    from botforge.discord.scope_surfaces import register_scope_sync

    async def _sync_games(guild_id: str, bot: Any) -> int:
        return await _sync_one_catalog(guild_id, bot, "game")

    async def _sync_mods(guild_id: str, bot: Any) -> int:
        return await _sync_one_catalog(guild_id, bot, "mod")

    register_scope_sync("games", _sync_games)
    register_scope_sync("mods", _sync_mods)


__all__ = [
    "ModAdminButton",
    "ModInstallButton",
    "ModRepairButton",
    "ModUninstallButton",
    "ModUninstallModal",
    "register_catalog_syncs",
    "register_mods_forum_items",
    "sync_catalogs",
]
