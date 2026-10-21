"""The games admin section: maps and map pools, per game (core, #133).

Registered into the /admin panel's Mods select under the ``games`` key:
the section manages the game catalogs — the maps (archive = disable,
the maps forum sync picks it up) and the map pools (create, rename,
edit the map list, archive). Everything goes through
``GameDataService`` (validated + audited); the section only renders
and collects input.

Namespace discipline: every component rides ``admin:pin:modgames:``
- deliberately NOT under ``admin:pin:mod:`` (the routing select's
prefix); one custom_id, one dispatch path, restart proof. Every
sub-view carries the back button.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import discord

from kingdoms.discord.admin_panel_mods import AdminModSection, register_admin_mod_section
from kingdoms.discord.commands_i18n import reply
from kingdoms.discord.ui.view_footer import add_view_footer
from kingdoms.discord.view_origin import from_pin

logger = logging.getLogger("kingdoms.games.admin_panel")

MOD_KEY = "games"
_NS = "admin:pin:modgames"
GAME_KEY = "aoe2"  # default context; the picker may switch it per view


async def _pool_admin_menu(pool_id: str, game_key: str) -> discord.ui.LayoutView:
    """Render the pool's admin menu (the post's two action lines)."""
    from kingdoms.discord.entity_admin_menu import build_entity_admin_menu

    return await build_entity_admin_menu("pool", pool_id, game_key, owned=True)


async def pools_admin_view(game_key: str, from_pin: bool = False) -> discord.ui.LayoutView:
    """Render the game's map pools management view (list + actions)."""
    service = _games_wiring()
    view = discord.ui.LayoutView(timeout=None)
    blocks: list[Any] = [discord.ui.TextDisplay(f"# Map pools ({game_key})")]
    if service is None:
        blocks.append(discord.ui.TextDisplay("Wiring indisponible."))
        view.add_item(discord.ui.Container(*blocks))
        view.add_item(_back_row())
        return add_view_footer(view, f"panel:game:{game_key}:pools:manage")
    pools = await service.list_map_pools(game_key)
    if not pools:
        blocks.append(discord.ui.TextDisplay("_Aucun map pool._"))
    else:
        lines = [f"- {p.name} ({len(p.map_ids)} maps)" for p in pools[:25]]
        blocks.append(discord.ui.TextDisplay("\n".join(lines)))
    view.add_item(discord.ui.Container(*blocks))
    delete_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    delete_row.add_item(GamesPoolDeleteSelect(game_key))
    view.add_item(delete_row)
    action_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    action_row.add_item(GamesPoolCopyButton(game_key))
    action_row.add_item(GamesPoolCreateButton(game_key))
    view.add_item(action_row)
    if not from_pin:
        back_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
        back_row.add_item(GamesPoolsBackButton(game_key))
        view.add_item(back_row)
    return add_view_footer(view, f"panel:game:{game_key}:pools:manage")


def _back_row() -> discord.ui.ActionRow[discord.ui.LayoutView]:
    """Build the back row of the games entry (returns to the main menu)."""
    row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    row.add_item(GamesBackButton())
    return row


def _games_wiring() -> Any | None:
    """Build the game-data wiring; None when Mongo is not configured."""
    from kingdoms.discord.wiring import build_games_service

    return build_games_service()


def _selected_values(interaction: discord.Interaction) -> list[str]:
    """Read a select interaction's chosen values (payload-shape proof).

    discord.py exposes the raw payload as a dict (``interaction.data``
    is an ``InteractionData`` where ``values`` hides in the dict form
    when the component type is a select) or as an object with a
    ``values`` attribute; every known shape is handled, and the select
    component itself is the final fallback (``self.item.values``).
    """
    data = getattr(interaction, "data", None)
    raw: Any = None
    if isinstance(data, dict):
        raw = data.get("values")
    if raw is None and data is not None:
        raw = getattr(data, "values", None)
    if raw is None and isinstance(data, dict):
        components = (data.get("data") or {}).get("values") or []
        if components and isinstance(components[0], (list, tuple)):
            raw = components[0]
    return [str(v) for v in raw] if isinstance(raw, (list, tuple)) else []



async def _gtr(interaction: discord.Interaction, key: str, fallback: str = "") -> str:
    """Render one ui.games key in the interaction's guild locale (EN fallback)."""
    from kingdoms.discord.commands_i18n import reply_locale

    catalog = getattr(interaction.client, "messages", None)
    if catalog is None:
        return fallback or key
    locale = await reply_locale(interaction)
    rendered = catalog.render(key, locale)
    if rendered == key:
        rendered = catalog.render(key, "en")
    return rendered if rendered != key else (fallback or key)




async def games_admin_entry(interaction: discord.Interaction) -> discord.ui.LayoutView:
    """Render the games admin section: game picker + per-game actions."""
    service = _games_wiring()
    view = discord.ui.LayoutView(timeout=None)
    blocks: list[Any] = [discord.ui.TextDisplay("# Games admin")]
    if service is None:
        wiring_note = await _gtr(
            interaction, "replies_shared.wiring_unavailable", "Wiring indisponible : Mongo n'est pas configuré."
        )
        blocks.append(discord.ui.TextDisplay(wiring_note))
        view.add_item(discord.ui.Container(*blocks, accent_colour=discord.Colour(0x5865F2)))
        view.add_item(_back_row())
        return add_view_footer(view, "panel:games:admin")
    from kingdoms.discord.wiring import granted_game_keys

    keys = list(await granted_game_keys("", tuple(await service.list_game_keys())))
    if not keys:
        no_game_note = await _gtr(
            interaction, "ui.games.no_game", "Aucun jeu connu — seede un catalogue."
        )
        blocks.append(discord.ui.TextDisplay(no_game_note))
    else:
        lines = []
        for key in keys[:15]:
            maps = await service.list_maps(key)
            pools = await service.list_map_pools(key)
            active = sum(1 for m in maps if m.archived_at is None)
            lines.append(f"- `{key}` : {active} maps actives, {len(pools)} pools")
        blocks.append(discord.ui.TextDisplay("\n".join(lines)))
    view.add_item(discord.ui.Container(*blocks, accent_colour=discord.Colour(0x5865F2)))
    select_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    select_row.add_item(GamesGameSelect())
    view.add_item(select_row)
    view.add_item(_back_row())
    return view


async def _refresh_pins_after_mutation(interaction: discord.Interaction) -> None:
    """Re-render the guild's pinned views after a games mutation (#257).

    No manual refresh affordance: the admin pin (and every surface
    registered in pinned_views) is edited in place, best-effort.
    """
    if interaction.guild_id is None:
        return
    try:
        from kingdoms.discord.pinned_views import refresh_registered_pins

        await refresh_registered_pins(str(interaction.guild_id))
    except Exception:
        logger.warning("pin refresh after mutation failed — best-effort", exc_info=True)


def _display_name(name: str) -> str:
    """Title-case a map's name for its post ('hideout' -> 'Hideout')."""
    return name[:1].upper() + name[1:] if name else name


async def civs_section_layout(game_key: str, guild_id: str) -> discord.ui.LayoutView:
    """Build the civs section's pinned layout (forum aoe2-factions).

    The section header (count + link) and its actions — the same
    content the admin panel renders inline, pinned in its forum.
    """
    service = _games_wiring()
    view = discord.ui.LayoutView(timeout=None)
    blocks: list[Any] = [discord.ui.TextDisplay(f"# Civs — {game_key}")]
    if service is None:
        blocks.append(discord.ui.TextDisplay("Wiring indisponible."))
        view.add_item(discord.ui.Container(*blocks))
        return add_view_footer(view, f"panel:game:{game_key}:civs")
    factions = await service.list_factions(game_key)
    blocks.append(discord.ui.TextDisplay(f"{len(factions)} civs"))
    view.add_item(discord.ui.Container(*blocks, accent_colour=discord.Colour(0x5865F2)))
    return view


async def maps_section_layout(game_key: str, guild_id: str) -> discord.ui.LayoutView:
    """Build the maps section's pinned layout (forum aoe2-maps).

    The section header (count of active maps) and the section's
    action buttons: import (single + bulk) and create.
    """
    service = _games_wiring()
    view = discord.ui.LayoutView(timeout=None)
    blocks: list[Any] = [discord.ui.TextDisplay(f"# Maps — {game_key}")]
    if service is None:
        blocks.append(discord.ui.TextDisplay("Wiring indisponible."))
        view.add_item(discord.ui.Container(*blocks))
        return add_view_footer(view, f"panel:game:{game_key}:maps")
    maps = await service.list_maps(game_key)
    active = [m for m in maps if m.archived_at is None]
    blocks.append(discord.ui.TextDisplay(f"{len(active)} maps actives"))
    view.add_item(discord.ui.Container(*blocks, accent_colour=discord.Colour(0x5865F2)))
    actions: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    actions.add_item(GamesMapImportButton(game_key))
    actions.add_item(GamesBulkImportButton(game_key))
    actions.add_item(GamesMapCreateButton(game_key))
    view.add_item(actions)
    return view


async def pools_section_layout(game_key: str, guild_id: str) -> discord.ui.LayoutView:
    """Build the map pools section's pinned layout (forum aoe2-map-pools).

    The section header (count) and the section's action buttons:
    copy, create and manage.
    """
    service = _games_wiring()
    view = discord.ui.LayoutView(timeout=None)
    blocks: list[Any] = [discord.ui.TextDisplay(f"# Map pools — {game_key}")]
    if service is None:
        blocks.append(discord.ui.TextDisplay("Wiring indisponible."))
        view.add_item(discord.ui.Container(*blocks))
        return add_view_footer(view, f"panel:game:{game_key}:pools")
    pools = await service.list_map_pools(game_key)
    blocks.append(discord.ui.TextDisplay(f"{len(pools)} map pools"))
    view.add_item(discord.ui.Container(*blocks, accent_colour=discord.Colour(0x5865F2)))
    actions: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    actions.add_item(GamesPoolCopyButton(game_key))
    actions.add_item(GamesPoolCreateButton(game_key))
    actions.add_item(GamesPoolsManageButton(game_key))
    view.add_item(actions)
    return view



class GamesGameSelect(
    discord.ui.DynamicItem[discord.ui.Select[Any]],
    template=rf"{_NS}:game:(?P<game_key>[a-z0-9_]+)",
):
    """Pick a game to manage; opens the game's sub-menu (maps/pools)."""

    def __init__(self, options: list[discord.SelectOption] | None = None) -> None:
        super().__init__(
            discord.ui.Select(
                custom_id=f"{_NS}:game:picker",
                options=options or [discord.SelectOption(label="No game", value="none")],
                placeholder="Gérer un jeu...",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> GamesGameSelect:
        """Rebuild the select's options at click time (granted games)."""
        from kingdoms.discord.wiring import granted_game_keys

        service = _games_wiring()
        options: list[discord.SelectOption] = []
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        catalog: tuple[str, ...] = ()
        if service is not None:
            catalog = tuple(await service.list_game_keys())
            if GAME_KEY not in catalog:
                catalog = (*catalog, GAME_KEY)
        for key in (await granted_game_keys(guild_id, catalog))[:25]:
            options.append(discord.SelectOption(label=key, value=key))
        return cls(options)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Answer the chosen game's sub-menu as a dedicated ephemeral view."""
        chosen = (_selected_values(interaction) or [""])[0]
        if not chosen or chosen == "none":
            await interaction.response.defer()
            return
        await interaction.response.send_message(
            view=await game_menu_view(chosen, from_pin=from_pin(interaction)),
            ephemeral=True,
        )


async def game_menu_view(game_key: str, from_pin: bool = False, guild_id: str = "") -> discord.ui.LayoutView:
    """One game's admin menu: a sectioned card, Civs / Maps / Map pools.

    Each section carries its count and its forum link in the header,
    then its action row with short contextual labels (i18n, every
    locale). The sections will grow (players, matches) — one section
    per domain, never a flat button pile.
    """
    service = _games_wiring()
    view = discord.ui.LayoutView(timeout=None)
    blocks: list[Any] = [discord.ui.TextDisplay(f"# {game_key.capitalize()} \u2014 Admin")]
    if service is None:
        blocks.append(discord.ui.TextDisplay("Wiring indisponible."))
        view.add_item(discord.ui.Container(*blocks))
        if not from_pin:
            view.add_item(_back_row())
        return add_view_footer(view, f"panel:game:{game_key}:menu")
    maps = await service.list_maps(game_key)
    pools = await service.list_map_pools(game_key)
    factions = await service.list_factions(game_key)
    active_maps = [m for m in maps if m.archived_at is None]
    civs_forum = _forum_id(guild_id, f"{game_key}-factions") if guild_id else ""
    maps_forum = _forum_id(guild_id, f"{game_key}-maps") if guild_id else ""
    pools_forum = _forum_id(guild_id, f"{game_key}-map-pools") if guild_id else ""
    blocks.extend(
        [
            discord.ui.Separator(),
            discord.ui.TextDisplay(f"## Civs \u2014 {len(factions)}\n" + (f"<#{civs_forum}>" if civs_forum else "")),
            discord.ui.Separator(),
            discord.ui.TextDisplay(
                f"## Maps \u2014 {len(active_maps)} actives\n"
                + (f"<#{maps_forum}>" if maps_forum else "")
            ),
            discord.ui.Separator(),
            discord.ui.TextDisplay(
                f"## Map pools \u2014 {len(pools)}\n"
                + (f"<#{pools_forum}>" if pools_forum else "")
            ),
        ]
    )
    view.add_item(discord.ui.Container(*blocks, accent_colour=discord.Colour(0x5865F2)))
    sections_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    sections_row.add_item(GamesCivsSectionButton(game_key))
    sections_row.add_item(GamesMapsSectionButton(game_key))
    sections_row.add_item(GamesPoolsSectionButton(game_key))
    view.add_item(sections_row)
    if not from_pin:
        view.add_item(_back_row())
    return view


class GamesPoolsManageButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:poolsmanage:(?P<game_key>[a-z0-9_]+)",
):
    """Open the game's map pools management view (list, edit, delete)."""

    def __init__(self, game_key: str) -> None:
        self.game_key = game_key
        super().__init__(
            discord.ui.Button(
                label="Gérer les pools",
                style=discord.ButtonStyle.primary,
                custom_id=f"{_NS}:poolsmanage:{game_key}"[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> GamesPoolsManageButton:
        """Rebuild from the wire (game key from the custom_id)."""
        del interaction, item
        return cls(match.group("game_key"))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the pools management view."""
        from kingdoms.discord.maps_pool_flow import _guard_admin

        if not await _guard_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.edit_original_response(
            view=await pools_admin_view(self.game_key, from_pin=from_pin(interaction))
        )


class GamesMapsManageButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:mapsmanage:(?P<game_key>[a-z0-9_]+)",
):
    """Open the game's maps management view (list, archive, delete)."""

    def __init__(self, game_key: str) -> None:
        self.game_key = game_key
        super().__init__(
            discord.ui.Button(
                label="Gérer les maps",
                style=discord.ButtonStyle.primary,
                custom_id=f"{_NS}:mapsmanage:{game_key}"[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> GamesMapsManageButton:
        """Rebuild from the wire (game key from the custom_id)."""
        del interaction, item
        return cls(match.group("game_key"))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the maps management view."""
        from kingdoms.discord.maps_pool_flow import _guard_admin

        if not await _guard_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        await interaction.edit_original_response(
            view=await maps_admin_view(self.game_key, from_pin=from_pin(interaction), guild_id=guild_id)
        )


async def civs_section_view(game_key: str) -> discord.ui.LayoutView:
    """Build the civs sub-menu: count, forum link, refresh entry point."""
    service = _games_wiring()
    view = discord.ui.LayoutView(timeout=None)
    blocks: list[Any] = [discord.ui.TextDisplay(f"# Civs ({game_key})")]
    if service is None:
        blocks.append(discord.ui.TextDisplay("Wiring indisponible."))
        view.add_item(discord.ui.Container(*blocks))
        return add_view_footer(view, f"panel:game:{game_key}:civs:manage")
    factions = await service.list_factions(game_key)
    blocks.append(discord.ui.TextDisplay(f"{len(factions)} civs"))
    view.add_item(discord.ui.Container(*blocks, accent_colour=discord.Colour(0x5865F2)))
    view.add_item(_back_row())
    return view


class GamesCivsSectionButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:section:civs:(?P<game_key>[a-z0-9_]+)",
):
    """Open the game's civs sub-menu."""

    def __init__(self, game_key: str) -> None:
        self.game_key = game_key
        super().__init__(
            discord.ui.Button(
                label="Civs",
                emoji="\U0001f9d9",
                style=discord.ButtonStyle.primary,
                custom_id=f"{_NS}:section:civs:{game_key}"[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> GamesCivsSectionButton:
        """Rebuild from the wire."""
        del interaction, item
        return cls(match.group("game_key"))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the civs sub-menu."""
        from kingdoms.discord.maps_pool_flow import _guard_admin

        if not await _guard_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.edit_original_response(view=await civs_section_view(self.game_key))


class GamesMapsSectionButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:section:maps:(?P<game_key>[a-z0-9_]+)",
):
    """Open the game's maps sub-menu (list, import, create, delete)."""

    def __init__(self, game_key: str) -> None:
        self.game_key = game_key
        super().__init__(
            discord.ui.Button(
                label="Maps",
                emoji="\U0001f5fa",
                style=discord.ButtonStyle.primary,
                custom_id=f"{_NS}:section:maps:{game_key}"[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> GamesMapsSectionButton:
        """Rebuild from the wire."""
        del interaction, item
        return cls(match.group("game_key"))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the maps sub-menu."""
        from kingdoms.discord.maps_pool_flow import _guard_admin

        if not await _guard_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        await interaction.edit_original_response(
            view=await maps_admin_view(self.game_key, from_pin=from_pin(interaction), guild_id=guild_id)
        )


class GamesPoolsSectionButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:section:pools:(?P<game_key>[a-z0-9_]+)",
):
    """Open the game's map pools sub-menu (list, copy, create, delete)."""

    def __init__(self, game_key: str) -> None:
        self.game_key = game_key
        super().__init__(
            discord.ui.Button(
                label="Map pools",
                emoji="\U0001f9f1",
                style=discord.ButtonStyle.primary,
                custom_id=f"{_NS}:section:pools:{game_key}"[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> GamesPoolsSectionButton:
        """Rebuild from the wire."""
        del interaction, item
        return cls(match.group("game_key"))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the pools sub-menu."""
        from kingdoms.discord.maps_pool_flow import _guard_admin

        if not await _guard_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.edit_original_response(
            view=await pools_admin_view(self.game_key, from_pin=from_pin(interaction))
        )


async def _forum_links(game_key: str, guild_id: str) -> str:
    """Mention links to the game's forums (civs, maps, pools)."""
    if not guild_id:
        return ""
    return " ".join(
        [
            f"\ud83e\uddd9 Civs : <#{_forum_id(guild_id, f'{game_key}-factions')}>",
            f"\ud83d\uddfa\ufe0f Maps : <#{_forum_id(guild_id, f'{game_key}-maps')}>",
            f"\ud83e\uddf1 Pools : <#{_forum_id(guild_id, f'{game_key}-map-pools')}>",
        ]
    )


def _forum_id(guild_id: str, forum_name: str) -> str:
    """Resolve a forum id by name (empty mention-safe id when absent)."""
    from kingdoms.discord.admin_panel_dynamic import _panel_client_ref

    client = _panel_client_ref()
    guild = client.get_guild(int(guild_id)) if client and guild_id.isdigit() else None
    forum = discord.utils.get(guild.forums, name=forum_name) if guild else None
    return str(forum.id) if forum is not None else ""



class GamesGrantedReopenButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:granted:reopen:(?P<game_key>[a-z0-9_]+)",
):
    """Re-open a granted game's sub-menu without re-selecting.

    A Discord select fires only on *change*: once a game is selected,
    dismissing the ephemeral leaves the option selected and picking it
    again does nothing. This button sits next to the select and always
    re-opens the last-chosen game's menu.
    """

    def __init__(self, game_key: str) -> None:
        self.game_key = game_key
        super().__init__(
            discord.ui.Button(
                label="Rouvrir le menu du jeu",
                style=discord.ButtonStyle.secondary,
                custom_id=f"{_NS}:granted:reopen:{game_key}"[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> GamesGrantedReopenButton:
        """Rebuild from the wire (game key from the custom_id)."""
        del interaction, item
        return cls(match.group("game_key"))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Re-open the game's sub-menu (deferred: the render can exceed 3s)."""
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        try:
            view = await game_menu_view(self.game_key, from_pin=from_pin(interaction), guild_id=guild_id)
        except Exception:
            logger.exception("GAMES GRANTED REOPEN: game menu render failed")
            await interaction.edit_original_response(
                view=discord.ui.LayoutView(
                    timeout=None
                )
            )
            return
        await interaction.edit_original_response(view=view)


class GamesGrantedSelect(
    discord.ui.DynamicItem[discord.ui.Select[Any]],
    template=rf"{_NS}:granted:games",
):
    """The admin menu's games entry: pick one granted game to manage."""

    def __init__(self, options: list[discord.SelectOption] | None = None) -> None:
        super().__init__(
            discord.ui.Select(
                custom_id=f"{_NS}:granted:games"[:100],
                options=options or [discord.SelectOption(label="No granted game", value="none")],
                placeholder="Gerer un jeu...",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> GamesGrantedSelect:
        """Rebuild the options at click time (the guild's granted games)."""
        del item, match
        from kingdoms.discord.wiring import granted_game_keys

        service = _games_wiring()
        catalog: tuple[str, ...] = ()
        if service is not None:
            catalog = tuple(await service.list_game_keys())
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        keys = await granted_game_keys(guild_id, catalog)
        options = [discord.SelectOption(label=key, value=key) for key in keys[:25]]
        return cls(options)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the chosen game's sub-menu (visible failure, never silent)."""
        chosen = (_selected_values(interaction) or list(self.item.values or [""]))[0]
        logger.info("GAMES GRANTED SELECT clicked (guild %s, chosen %r)", interaction.guild_id, chosen)
        if not chosen or chosen == "none":
            await interaction.response.defer()
            return
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        try:
            view = await game_menu_view(chosen, from_pin=from_pin(interaction), guild_id=guild_id)
        except Exception:
            logger.exception("GAMES GRANTED SELECT: game menu render failed")
            await interaction.response.send_message(
                "Le menu du jeu a echoue a s'ouvrir (voir les logs).", ephemeral=True
            )
            return
        try:
            await interaction.response.send_message(view=view, ephemeral=True)
        except Exception:
            logger.exception("GAMES GRANTED SELECT: answer failed")
            await interaction.followup.send(
                "Le menu du jeu n'a pas pu etre envoye (voir les logs).", ephemeral=True
            )


class GamesBackButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:back",
):
    """Return to the games entry view."""

    def __init__(self) -> None:
        super().__init__(
            discord.ui.Button(
                label="Back",
                style=discord.ButtonStyle.secondary,
                custom_id=f"{_NS}:back",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> GamesBackButton:
        """Rebuild the item from the wire."""
        del interaction, item, match
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        """Return to the pinned main menu (top-level back).

        The games entry used to loop back to itself — there was no way
        back to the admin main menu from the games section.
        """
        from kingdoms.discord.admin_persistent import _handle_back

        await _handle_back(interaction)


class GamesMapsButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:maps:(?P<game_key>[a-z0-9_]+)",
):
    """Open the game's maps sub-view."""

    def __init__(self, game_key: str) -> None:
        self.game_key = game_key
        super().__init__(
            discord.ui.Button(
                label="Gérer les maps",
                style=discord.ButtonStyle.primary,
                custom_id=f"{_NS}:maps:{game_key}"[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> GamesMapsButton:
        """Rebuild the item from the wire (game key from the custom_id)."""
        del interaction, item
        return cls(match.group("game_key"))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Render the maps sub-view (deferred: the render can exceed 3s)."""
        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.edit_original_response(
            view=await maps_admin_view(self.game_key, from_pin=from_pin(interaction))
        )


class GamesPoolsButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:pools:(?P<game_key>[a-z0-9_]+)",
):
    """Open the game's map pools sub-view."""

    def __init__(self, game_key: str) -> None:
        self.game_key = game_key
        super().__init__(
            discord.ui.Button(
                label="Gérer les map pools",
                style=discord.ButtonStyle.primary,
                custom_id=f"{_NS}:pools:{game_key}"[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> GamesPoolsButton:
        """Rebuild the item from the wire (game key from the custom_id)."""
        del interaction, item
        return cls(match.group("game_key"))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Render the pools sub-view (deferred: the render can exceed 3s)."""
        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.edit_original_response(
            view=await pools_admin_view(self.game_key, from_pin=from_pin(interaction))
        )


async def maps_admin_view(game_key: str, from_pin: bool = False, guild_id: str = "") -> discord.ui.LayoutView:
    """Render the game's maps sub-view: the catalog with archive toggles."""
    service = _games_wiring()
    view = discord.ui.LayoutView(timeout=None)
    blocks: list[Any] = [discord.ui.TextDisplay(f"# Maps ({game_key})")]
    if service is None:
        blocks.append(discord.ui.TextDisplay("Wiring indisponible."))
        view.add_item(discord.ui.Container(*blocks))
        view.add_item(_back_row())
        return add_view_footer(view, f"panel:game:{game_key}:maps:manage")
    maps = await service.list_maps(game_key)
    if not maps:
        blocks.append(discord.ui.TextDisplay("_No map — seed the catalog._"))
    else:
        lines = []
        for m in maps[:20]:
            state = "desactivee" if m.archived_at is not None else "active"
            post = "post ok" if m.forum_message_id else "post manquant"
            lines.append(f"- {m.name} ({state}, {post})")
        blocks.append(discord.ui.TextDisplay("\n".join(lines)))
    view.add_item(discord.ui.Container(*blocks))
    select_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    select_row.add_item(GamesMapArchiveSelect(game_key))
    view.add_item(select_row)
    actions_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    actions_row.add_item(GamesMapImportButton(game_key))
    actions_row.add_item(GamesBulkImportButton(game_key))
    actions_row.add_item(GamesMapCreateButton(game_key))
    view.add_item(actions_row)
    delete_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    delete_row.add_item(GamesMapDeleteSelect(game_key))
    view.add_item(delete_row)
    if not from_pin:
        view.add_item(GamesMapsBackButton(game_key))
    return view


class GamesMapCreateButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:maps:create:(?P<scope>global|guild):(?P<game_key>[a-z0-9_]+)",
):
    """Open the map-creation modal; the map lands in the maps forum."""

    def __init__(self, game_key: str, global_scope: bool = False) -> None:
        self.game_key = game_key
        self.global_scope = global_scope
        label = "+ Map globale" if global_scope else "Créer une map"
        custom_id = f"{_NS}:maps:create:{'global' if global_scope else 'guild'}:{game_key}"
        super().__init__(
            discord.ui.Button(
                label=label, emoji="\u2795", style=discord.ButtonStyle.success, custom_id=custom_id[:100]
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> GamesMapCreateButton:
        """Rebuild the item from the wire (game key from the custom_id)."""
        del interaction, item
        return cls(match.group("game_key"), global_scope=match.group("scope") == "global")

    async def callback(self, interaction: discord.Interaction) -> None:
        """Guard the click, then open the creation modal.

        The global-scope button is bot-admin only: a global map syncs
        into every guild's forum, so only platform admins may add one.
        """
        from kingdoms.discord.maps_pool_flow import _guard_admin

        if not await _guard_admin(interaction):
            return
        if self.global_scope:
            from kingdoms.discord.guards import is_bot_admin

            admins = getattr(getattr(interaction.client, "status_service", None), "bot_admins", ())
            if not is_bot_admin(getattr(interaction.user, "id", None), tuple(admins)):
                await interaction.response.send_message(
                    "Les maps globales sont reservees aux bot admins.", ephemeral=True
                )
                return
        await interaction.response.send_modal(GamesMapCreateModal(self.game_key, global_scope=self.global_scope))


class GamesMapCreateModal(discord.ui.Modal):
    """The map-creation form: name, filename, description."""

    def __init__(self, game_key: str, global_scope: bool = False) -> None:
        self.game_key = game_key
        self.global_scope = global_scope
        super().__init__(title="Créer une map globale" if global_scope else "Créer une map", timeout=None)
        self.name: discord.ui.TextInput[Any] = discord.ui.TextInput(label="Map name", max_length=64, required=True)
        self.filename: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label="File (rms/txt filename)", max_length=128, required=False
        )
        self.description: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label="Description", max_length=256, required=False
        )
        self.add_item(self.name)
        self.add_item(self.filename)
        self.add_item(self.description)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Create the map through the service, confirm.

        Guild admins create guild-local maps (``owner_guild_id``); only
        bot admins may create global ones (``None`` scope), so a global
        map is modifiable by admins alone.
        """
        service = _games_wiring()
        if service is None:
            await interaction.response.send_message(
                await reply(interaction, "replies_shared.wiring_unavailable"), ephemeral=True
            )
            return
        name = str(self.name.value or "").strip()
        if not name:
            await interaction.response.send_message(
                await reply(interaction, "replies_shared.name_required"), ephemeral=True
            )
            return
        from kingdoms.discord.guards import is_bot_admin

        admins = getattr(getattr(interaction.client, "status_service", None), "bot_admins", ())
        guild_id = str(interaction.guild_id) if interaction.guild_id is not None else None
        is_bot_admin_click = is_bot_admin(getattr(interaction.user, "id", None), tuple(admins))
        owner_guild_id = None if is_bot_admin_click and self.global_scope else guild_id
        description = str(self.description.value or "").strip()
        filename = str(self.filename.value or "").strip() or name
        seed = None
        if owner_guild_id is not None:
            from kingdoms.mapsdata.seed import LiquipediaUnconfiguredError, fetch_map_seed

            try:
                seed = await fetch_map_seed(name)
            except LiquipediaUnconfiguredError:
                seed = None
            if seed is not None:
                description = description or seed.description
                filename = seed.name.replace(" ", "_").lower()
        try:
            entry = await service.create_map(
                self.game_key,
                name,
                filename=filename,
                description=description,
                owner_guild_id=owner_guild_id,
                resource_url=seed.resource_url if seed is not None else "",
            )
            if seed is not None and seed.image_url:
                from kingdoms.discord.content_posts import content_service

                content = content_service()
                if content is not None:
                    await content.store(
                        {
                            "entity_id": entry.id,
                            "locale": "en",
                            "name": entry.name,
                            "summary": seed.description,
                            "source_url": seed.resource_url,
                            "image_url": seed.image_url,
                            "provider": "liquipedia",
                        }
                    )
        except Exception:
            logger.exception("GAMES ADMIN: map creation failed")
            await interaction.response.send_message(
                await reply(interaction, "replies_shared.import_failed"), ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"Map **{entry.name}** creee - elle apparaitra dans le forum maps.", ephemeral=True
        )


class GamesMapArchiveSelect(
    discord.ui.DynamicItem[discord.ui.Select[Any]],
    template=rf"{_NS}:maps:archive:(?P<game_key>[a-z0-9_]+)",
):
    """Toggle a map's archived state (disable/enable) from the select."""

    def __init__(self, game_key: str, options: list[discord.SelectOption] | None = None) -> None:
        self.game_key = game_key
        super().__init__(
            discord.ui.Select(
                custom_id=f"{_NS}:maps:archive:{game_key}"[:100],
                options=options or [discord.SelectOption(label="Aucune map", value="none")],
                placeholder="Activer / désactiver une map...",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> GamesMapArchiveSelect:
        """Rebuild the select's options at click time."""
        service = _games_wiring()
        game_key = match.group("game_key")
        options: list[discord.SelectOption] = []
        if service is not None:
            maps = await service.list_maps(game_key)
            options = [
                discord.SelectOption(
                    label=f"{m.name} ({'desactivee' if m.archived_at else 'active'})",
                    value=m.id,
                )
                for m in maps[:25]
            ]
        return cls(game_key, options)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Toggle the map's archived state, audit, re-render."""
        service = _games_wiring()
        if service is None:
            await interaction.response.send_message(
                await reply(interaction, "replies_shared.wiring_unavailable"), ephemeral=True
            )
            return
        chosen = (_selected_values(interaction) or [""])[0]
        if not chosen or chosen == "none":
            await interaction.response.defer()
            return
        entry = await service.get_map(chosen)
        if entry is None:
            await interaction.response.send_message(
                await reply(interaction, "replies_shared.map_not_found"), ephemeral=True
            )
            return
        if entry.archived_at is not None:
            from kingdoms.core.services.game_data import MAPS_COLLECTION

            restored = entry.model_copy(update={"archived_at": None})
            await service._db.upsert_entry(MAPS_COLLECTION, restored.to_mongo())
            await service._audit_record("map.restore", {"map_id": chosen})
            message = f"Map **{entry.name}** reactivee."
        else:
            await service.archive_map(chosen)
            message = f"Map **{entry.name}** desactivee (archivee)."
        await interaction.response.edit_message(
            view=await maps_admin_view(self.game_key, from_pin=from_pin(interaction))
        )
        await interaction.followup.send(message, ephemeral=True)
        await _refresh_pins_after_mutation(interaction)


class GamesPoolDeleteSelect(
    discord.ui.DynamicItem[discord.ui.Select[Any]],
    template=rf"{_NS}:pools:deleteselect:(?P<game_key>[a-z0-9_]+)",
):
    """Delete (archive) a pool straight from the list."""

    def __init__(self, game_key: str, options: list[discord.SelectOption] | None = None) -> None:
        self.game_key = game_key
        super().__init__(
            discord.ui.Select(
                custom_id=f"{_NS}:pools:deleteselect:{game_key}"[:100],
                options=options or [discord.SelectOption(label="Aucun pool", value="none")],
                placeholder="Supprimer un pool...",
                max_values=1,
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> GamesPoolDeleteSelect:
        """Rebuild the options at click time (the active pools)."""
        del interaction, item
        service = _games_wiring()
        options: list[discord.SelectOption] = []
        if service is not None:
            pools = await service.list_map_pools(match.group("game_key"))
            options = [
                discord.SelectOption(label=p.name, value=p.id) for p in pools[:25]
            ]
        return cls(match.group("game_key"), options or None)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Guard, resolve the entry, open the confirm view."""
        from kingdoms.discord.maps_pool_flow import _guard_admin

        if not await _guard_admin(interaction):
            return
        chosen = (_selected_values(interaction) or [""])[0]
        if not chosen or chosen == "none":
            await interaction.response.defer()
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        service = _games_wiring()
        if service is None:
            await interaction.followup.send((await reply(interaction, "ui.flows.wiring_unavailable")), ephemeral=True)
            return
        entry = await service.get_map_pool(chosen)
        if entry is None:
            await interaction.followup.send((await reply(interaction, "ui.flows.entry_not_found")), ephemeral=True)
            return
        from kingdoms.discord.maps_pool_flow import _pool_delete_confirm_view

        await interaction.followup.send(
            view=_pool_delete_confirm_view(chosen, entry.name),
            ephemeral=True,
        )

class GamesMapDeleteSelect(
    discord.ui.DynamicItem[discord.ui.Select[Any]],
    template=rf"{_NS}:maps:delete:(?P<game_key>[a-z0-9_]+)",
):
    """Hard-delete a map record (orphan cleanup; no forum thread needed)."""

    def __init__(self, game_key: str, options: list[discord.SelectOption] | None = None) -> None:
        self.game_key = game_key
        super().__init__(
            discord.ui.Select(
                custom_id=f"{_NS}:maps:delete:{game_key}"[:100],
                options=options or [discord.SelectOption(label="Aucune map", value="none")],
                placeholder="Supprimer une map...",
                max_values=1,
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> GamesMapDeleteSelect:
        """Rebuild the options at click time (the game's maps)."""
        del interaction, item
        service = _games_wiring()
        options: list[discord.SelectOption] = []
        if service is not None:
            maps = await service.list_maps(match.group("game_key"))
            options = [
                discord.SelectOption(label=m.name, value=m.id) for m in maps[:25] if m.archived_at is None
            ]
        return cls(match.group("game_key"), options or None)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Guard, resolve the entry, open the confirm view."""
        from kingdoms.discord.maps_pool_flow import _guard_admin

        if not await _guard_admin(interaction):
            return
        chosen = (_selected_values(interaction) or [""])[0]
        if not chosen or chosen == "none":
            await interaction.response.defer()
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        service = _games_wiring()
        if service is None:
            await interaction.followup.send((await reply(interaction, "ui.flows.wiring_unavailable")), ephemeral=True)
            return
        entry = await service.get_map(chosen)
        if entry is None:
            await interaction.followup.send((await reply(interaction, "ui.flows.entry_not_found")), ephemeral=True)
            return
        from kingdoms.discord.maps_pool_flow import _map_delete_confirm_view

        await interaction.followup.send(
            view=_map_delete_confirm_view(chosen, self.game_key, entry.name),
            ephemeral=True,
        )

class GamesPoolCreateButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:pools:create:(?P<game_key>[a-z0-9_]+)",
):
    """Open the pool-creation modal (name)."""

    def __init__(self, game_key: str) -> None:
        self.game_key = game_key
        super().__init__(
            discord.ui.Button(
                label="Créer un map pool",
                emoji="\u2795",
                style=discord.ButtonStyle.success,
                custom_id=f"{_NS}:pools:create:{game_key}"[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> GamesPoolCreateButton:
        """Rebuild the item from the wire (game key from the custom_id)."""
        del interaction, item
        return cls(match.group("game_key"))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the creation modal."""
        await interaction.response.send_modal(GamesPoolCreateModal(self.game_key))


class GamesPoolCreateModal(discord.ui.Modal):
    """The pool-creation form: name, then maps are added via the edit view."""

    def __init__(self, game_key: str) -> None:
        self.game_key = game_key
        super().__init__(title="Créer un map pool", timeout=None)
        self.name: discord.ui.TextInput[Any] = discord.ui.TextInput(label="Nom du pool", max_length=64, required=True)
        self.add_item(self.name)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Create the pool (with the optional first map), confirm."""
        service = _games_wiring()
        if service is None:
            await interaction.response.send_message(
                await reply(interaction, "replies_shared.wiring_unavailable"), ephemeral=True
            )
            return
        try:
            pool = await service.create_map_pool(
                self.game_key, str(self.name.value).strip(), map_ids=()
            )
        except Exception:
            logger.exception("GAMES ADMIN: pool creation failed")
            await interaction.response.send_message(
                await reply(interaction, "replies_shared.creation_failed"), ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"Pool **{pool.name}** créé — synchronisation du forum…", ephemeral=True
        )
        try:
            from kingdoms.discord.pools_forum import sync_pools_forum

            if interaction.guild_id is not None:
                await sync_pools_forum(str(interaction.guild_id), interaction.client)
        except Exception:
            logger.warning("GAMES ADMIN: pools forum sync after create failed — best-effort")
        pool_link = ""
        fresh = await service.get_map_pool(pool.id)
        if fresh is not None and getattr(fresh, "forum_message_id", None) and interaction.guild_id:
            pool_link = (
                f" — son post : https://discord.com/channels/{interaction.guild_id}/"
                f"{fresh.forum_message_id}/{fresh.forum_message_id}"
            )
        await interaction.edit_original_response(
            content=f"Pool **{pool.name}** créé{pool_link or ' — son post est dans le forum des map pools.'}"
        )
        await _refresh_pins_after_mutation(interaction)


class GamesPoolAddByIdButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:pools:addbyid:(?P<pool_id>.+)",
):
    """Open the add-map-by-id modal — the over-25 catalog path."""

    def __init__(self, pool_id: str) -> None:
        self.pool_id = pool_id
        super().__init__(
            discord.ui.Button(
                label="Ajouter une map (id)",
                emoji="\U0001f4cd",
                style=discord.ButtonStyle.secondary,
                custom_id=f"{_NS}:pools:addbyid:{pool_id}"[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> GamesPoolAddByIdButton:
        """Rebuild from the wire (pool id from the custom_id)."""
        del interaction, item
        return cls(match.group("pool_id"))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Guard, then open the by-id modal."""
        from kingdoms.discord.maps_pool_flow import PoolAddMapByIdModal, _guard_admin

        if not await _guard_admin(interaction):
            return
        await interaction.response.send_modal(PoolAddMapByIdModal(self.pool_id))


async def _finish_pool_transition(
    interaction: discord.Interaction, pool: Any, *, answer_menu: bool = True
) -> None:
    """After a pool mutation: re-render the forum post, then the menu.

    The post is only a mirror of the database record: the pools forum
    sync re-edits it (state badge, title) right away, and the admin
    menu re-renders with the pool's new state and buttons.
    """
    if interaction.guild_id is not None:
        try:
            from kingdoms.discord.pools_forum import sync_pools_forum

            await sync_pools_forum(str(interaction.guild_id), interaction.client)
        except Exception:
            logger.warning("pool transition: pools forum sync failed — best-effort", exc_info=True)
    if answer_menu:
        view = await _pool_admin_menu(pool.id, pool.game_key)
        if interaction.response.is_done():
            await interaction.followup.send(view=view, ephemeral=True)
        else:
            await interaction.response.edit_message(view=view)


class GamesPoolRenameButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:pools:rename:(?P<pool_id>.+)",
):
    """Open the rename modal; the pool id rides the custom_id."""

    def __init__(self, pool_id: str) -> None:
        self.pool_id = pool_id
        super().__init__(
            discord.ui.Button(
                label="Rename",
                emoji="\u270f",
                style=discord.ButtonStyle.primary,
                custom_id=f"{_NS}:pools:rename:{pool_id}"[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> GamesPoolRenameButton:
        """Rebuild the item from the wire (pool id from the custom_id)."""
        del interaction, item
        return cls(match.group("pool_id"))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the rename modal."""
        if not self.pool_id:
            await interaction.response.send_message("Pool introuvable - rouvre l'editeur.", ephemeral=True)
            return
        await interaction.response.send_modal(GamesPoolRenameModal(self.pool_id))


class GamesPoolRenameModal(discord.ui.Modal):
    """The pool rename form."""

    def __init__(self, pool_id: str) -> None:
        self.pool_id = pool_id
        super().__init__(title="Renommer le pool", timeout=None)
        self.name: discord.ui.TextInput[Any] = discord.ui.TextInput(label="New name", max_length=64, required=True)
        self.add_item(self.name)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Rename the pool, audit, re-render the editor."""
        service = _games_wiring()
        if service is None:
            await interaction.response.send_message(
                await reply(interaction, "replies_shared.wiring_unavailable"), ephemeral=True
            )
            return
        try:
            pool = await service.update_map_pool(self.pool_id, name=str(self.name.value).strip())
        except Exception:
            logger.exception("GAMES ADMIN: pool rename failed")
            await interaction.response.send_message("Renommage echoue (voir les logs).", ephemeral=True)
            return
        fresh = await service.get_map_pool(pool.id)
        await _finish_pool_transition(interaction, fresh if fresh is not None else pool)
        if interaction.guild_id is not None:
            try:
                from kingdoms.discord.pools_forum import sync_pools_forum

                await sync_pools_forum(str(interaction.guild_id), interaction.client)
            except Exception:
                logger.warning("pool rename: pools forum sync failed — best-effort", exc_info=True)
        await interaction.followup.send(f"Pool renommé **{pool.name}** — son post est à jour.", ephemeral=True)


class GamesPoolArchiveButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:pools:archive:(?P<pool_id>.+)",
):
    """Archive (delete) the pool, unless it is the active pool of a mod."""

    def __init__(self, pool_id: str) -> None:
        self.pool_id = pool_id
        super().__init__(
            discord.ui.Button(
                label="Archiver",
                emoji="\U0001f4e6",
                style=discord.ButtonStyle.secondary,
                custom_id=f"{_NS}:pools:archive:{pool_id}"[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> GamesPoolArchiveButton:
        """Rebuild the item from the wire (pool id from the custom_id)."""
        del interaction, item
        return cls(match.group("pool_id"))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Archive the pool, then return to the pools list."""
        from kingdoms.discord.maps_pool_flow import _guard_admin

        if not await _guard_admin(interaction):
            return
        service = _games_wiring()
        if service is None or not self.pool_id:
            await interaction.response.send_message(
                await reply(interaction, "replies_shared.pool_not_found"), ephemeral=True
            )
            return
        pool = await service.get_map_pool(self.pool_id)
        if pool is None:
            await interaction.response.send_message(
                await reply(interaction, "replies_shared.pool_not_found"), ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            pool = await service.archive_map_pool(self.pool_id)
        except Exception:
            logger.exception("GAMES ADMIN: pool archive failed")
            await interaction.followup.send(
                "Archivage \u00e9chou\u00e9 (pool actif ? voir les logs).", ephemeral=True
            )
            return
        # the post is a mirror: the sync removes the archived pool's thread
        await _finish_pool_transition(interaction, pool, answer_menu=False)
        await interaction.followup.send(view=await pools_admin_view(pool.game_key), ephemeral=True)
        await interaction.followup.send("Pool archiv\u00e9.", ephemeral=True)
        await _refresh_pins_after_mutation(interaction)




class GamesPoolToDraftButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:pools:todraft:(?P<pool_id>.+)",
):
    """Move a published pool back to edition (published -> draft)."""

    def __init__(self, pool_id: str) -> None:
        self.pool_id = pool_id
        super().__init__(
            discord.ui.Button(
                label="\u00c9diter",
                emoji="\u270f",
                style=discord.ButtonStyle.secondary,
                custom_id=f"{_NS}:pools:todraft:{pool_id}"[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> GamesPoolToDraftButton:
        """Rebuild from the wire (pool id from the custom_id)."""
        del interaction, item
        return cls(match.group("pool_id"))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Guard, move the pool to draft, re-render the admin menu."""
        from kingdoms.discord.maps_pool_flow import _guard_admin

        if not await _guard_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        service = _games_wiring()
        if service is None or not self.pool_id:
            await interaction.followup.send(
                await reply(interaction, "replies_shared.wiring_unavailable"), ephemeral=True
            )
            return
        try:
            pool = await service.transition_map_pool(self.pool_id, "draft")
        except Exception:
            logger.exception("GAMES ADMIN: pool to-draft failed (%s)", self.pool_id)
            await interaction.followup.send(
                await reply(interaction, "replies_shared.transition_failed"), ephemeral=True
            )
            return
        await _finish_pool_transition(interaction, pool)


class GamesPoolPublishButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:pools:publish:(?P<pool_id>.+)",
):
    """Publish a draft pool (draft -> published)."""

    def __init__(self, pool_id: str) -> None:
        self.pool_id = pool_id
        super().__init__(
            discord.ui.Button(
                label="Publier",
                emoji="\U0001f4e3",
                style=discord.ButtonStyle.success,
                custom_id=f"{_NS}:pools:publish:{pool_id}"[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> GamesPoolPublishButton:
        """Rebuild the item from the wire (pool id from the custom_id)."""
        del interaction, item
        return cls(match.group("pool_id"))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Publish the draft pool, then re-render the editor."""
        from kingdoms.discord.maps_pool_flow import _guard_admin

        if not await _guard_admin(interaction):
            return
        service = _games_wiring()
        pool = await service.get_map_pool(self.pool_id) if service is not None else None
        if pool is None:
            await interaction.response.send_message(
                await reply(interaction, "replies_shared.pool_not_found"), ephemeral=True
            )
            return
        if not pool.map_ids:
            await interaction.response.send_message(
                "Un pool vide ne peut pas \u00eatre publi\u00e9 \u2014 ajoute des maps d'abord.",
                ephemeral=True,
            )
            return
        if service is None:
            await interaction.response.send_message(
                await reply(interaction, "replies_shared.wiring_unavailable"), ephemeral=True
            )
            return
        try:
            await service.transition_map_pool(pool.id, "published")
        except Exception:
            logger.exception("GAMES ADMIN: pool publish failed")
            await interaction.response.send_message(
                await reply(interaction, "replies_shared.transition_failed"), ephemeral=True
            )
            return
        await interaction.response.edit_message(
            view=await _pool_admin_menu(pool.id, pool.game_key)
        )


class GamesPoolHardDeleteButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:pools:harddelete:(?P<pool_id>.+)",
):
    """Delete a pool entirely \u2014 only a draft pool never used on a ladder."""

    def __init__(self, pool_id: str) -> None:
        self.pool_id = pool_id
        super().__init__(
            discord.ui.Button(
                label="Supprimer",
                emoji="\U0001F5D1",
                style=discord.ButtonStyle.danger,
                custom_id=f"{_NS}:pools:harddelete:{pool_id}"[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> GamesPoolHardDeleteButton:
        """Rebuild the item from the wire (pool id from the custom_id)."""
        del interaction, item
        return cls(match.group("pool_id"))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Delete the never-used draft pool, then return to the list."""
        from kingdoms.discord.maps_pool_flow import _guard_admin

        if not await _guard_admin(interaction):
            return
        service = _games_wiring()
        pool = await service.get_map_pool(self.pool_id) if service is not None else None
        if pool is None:
            await interaction.response.send_message(
                await reply(interaction, "replies_shared.pool_not_found"), ephemeral=True
            )
            return
        if pool.state != "draft" or pool.ever_activated:
            await interaction.response.send_message(
                "Seul un pool en \u00e9dition jamais utilis\u00e9 peut \u00eatre supprim\u00e9 totalement \u2014 "
                "archive-le (r\u00e9versible) pour les autres cas.",
                ephemeral=True,
            )
            return
        if service is None:
            await interaction.response.send_message(
                await reply(interaction, "replies_shared.wiring_unavailable"), ephemeral=True
            )
            return
        try:
            await service.hard_delete_map_pool(pool.id)
        except Exception:
            logger.exception("GAMES ADMIN: pool hard delete failed")
            await interaction.response.send_message(
                await reply(interaction, "replies_shared.action_failed"), ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        # the post is a mirror: the sync deletes the gone pool's thread
        await _finish_pool_transition(interaction, pool, answer_menu=False)
        await interaction.followup.send(view=await pools_admin_view(pool.game_key), ephemeral=True)
        await interaction.followup.send(
            f"Pool **{pool.name}** supprim\u00e9 totalement (aucune r\u00e9f\u00e9rence ne pointait dessus).",
            ephemeral=True,
        )
        await _refresh_pins_after_mutation(interaction)


PAGE_SIZE = 25  # Discord's hard cap on select options


def _page_options(pool: Any, maps: list[Any], page: int) -> list[discord.SelectOption]:
    """Build the page's toggle options (25 max, membership shown)."""
    start = page * PAGE_SIZE
    return [
        discord.SelectOption(
            label=f"{m.name}{' [dans le pool]' if pool and m.id in pool.map_ids else ''}",
            value=m.id,
        )
        for m in maps[start : start + PAGE_SIZE]
    ]


class GamesPoolMapPageSelect(
    discord.ui.DynamicItem[discord.ui.Select[Any]],
    template=rf"{_NS}:pools:page:(?P<page>[0-9]+):(?P<pool_id>.+)",
):
    """Turn to another page of the pool's map picker (25 maps per page)."""

    def __init__(self, pool_id: str, page_count: int, page: int = 0) -> None:
        self.pool_id = pool_id
        self.page_count = page_count
        super().__init__(
            discord.ui.Select(
                custom_id=f"{_NS}:pools:page:{page}:{pool_id}"[:100],
                options=[
                    discord.SelectOption(label=f"Maps {i * 25 + 1}-{min((i + 1) * 25, page_count * 25)}", value=str(i))
                    for i in range(page_count)
                ],
                placeholder="Map picker page...",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> GamesPoolMapPageSelect:
        """Rebuild the pager from the wire; the page rides the id."""
        service = _games_wiring()
        pool_id = match.group("pool_id")
        page_count = 1
        if service is not None and pool_id:
            pool = await service.get_map_pool(pool_id)
            if pool is not None:
                maps = [m for m in await service.list_maps(pool.game_key) if m.archived_at is None]
                page_count = max(1, -(-len(maps) // 25))
        return cls(pool_id, page_count)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Re-render the editor on the chosen page."""
        service = _games_wiring()
        pool = await service.get_map_pool(self.pool_id) if service is not None else None
        if pool is None:
            await interaction.response.send_message(
                await reply(interaction, "replies_shared.pool_not_found"), ephemeral=True
            )
            return
        await interaction.response.edit_message(
            view=await _pool_admin_menu(self.pool_id, pool.game_key)
        )


class GamesPoolMapToggle(
    discord.ui.DynamicItem[discord.ui.Select[Any]],
    template=rf"{_NS}:pools:maps:(?P<page>[0-9]+):(?P<pool_id>.+)",
):
    """Add or remove a map from the pool (one select, toggle semantics, paged)."""

    def __init__(self, pool_id: str, page: int = 0, options: list[discord.SelectOption] | None = None) -> None:
        self.pool_id = pool_id
        self.page = page
        super().__init__(
            discord.ui.Select(
                custom_id=f"{_NS}:pools:maps:{page}:{pool_id}"[:100],
                options=options or [discord.SelectOption(label="Aucune map", value="none")],
                placeholder="Ajouter / retirer une map...",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> GamesPoolMapToggle:
        """Rebuild the select's options at click time; page and pool from state."""
        service = _games_wiring()
        pool_id = match.group("pool_id")
        page = int(match.group("page"))
        options: list[discord.SelectOption] = []
        if service is not None and pool_id:
            pool = await service.get_map_pool(pool_id)
            maps = [m for m in await service.list_maps(pool.game_key if pool else GAME_KEY) if m.archived_at is None]
            options = _page_options(pool, maps, page)
        return cls(pool_id, page, options)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Toggle the chosen map in the pool, re-render the editor."""
        service = _games_wiring()
        if service is None or not self.pool_id:
            await interaction.response.send_message(
                await reply(interaction, "replies_shared.pool_not_found"), ephemeral=True
            )
            return
        chosen = (_selected_values(interaction) or [""])[0]
        if not chosen or chosen == "none":
            await interaction.response.defer()
            return
        pool = await service.get_map_pool(self.pool_id)
        if pool is None:
            await interaction.response.send_message(
                await reply(interaction, "replies_shared.pool_not_found"), ephemeral=True
            )
            return
        current = list(pool.map_ids)
        if chosen in current:
            current.remove(chosen)
            removed = True
        else:
            current.append(chosen)
            removed = False
        try:
            await service.update_map_pool(self.pool_id, map_ids=tuple(current))
        except Exception:
            logger.exception("GAMES ADMIN: pool map toggle failed")
            await interaction.response.send_message(
                await reply(interaction, "replies_shared.edit_failed"), ephemeral=True
            )
            return
        await interaction.response.edit_message(
            view=await _pool_admin_menu(self.pool_id, pool.game_key)
        )
        await interaction.followup.send("Map retiree." if removed else "Map ajoutee.", ephemeral=True)


class GamesPoolMapRemoveSelect(
    discord.ui.DynamicItem[discord.ui.Select[Any]],
    template=rf"{_NS}:pools:remove:(?P<pool_id>.+)",
):
    """Remove maps from the pool (multi-select, pool members only).

    The pool caps at 25 maps by contract, so its members always fit
    one select; picking one or more removes them all, then the
    editor re-renders.
    """

    def __init__(self, pool_id: str, options: list[discord.SelectOption] | None = None) -> None:
        self.pool_id = pool_id
        super().__init__(
            discord.ui.Select(
                custom_id=f"{_NS}:pools:remove:{pool_id}"[:100],
                options=options or [discord.SelectOption(label="Aucune map", value="none")],
                placeholder="Retirer des maps du pool...",
                min_values=1,
                max_values=min(25, len(options)) if options else 1,
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> GamesPoolMapRemoveSelect:
        """Rebuild the options at click time (the pool's members)."""
        del interaction, item
        service = _games_wiring()
        options: list[discord.SelectOption] = []
        if service is not None:
            pool = await service.get_map_pool(match.group("pool_id"))
            if pool is not None:
                for map_id in pool.map_ids:
                    entry = await service.get_map(map_id)
                    if entry is not None and entry.archived_at is None:
                        options.append(discord.SelectOption(label=entry.name, value=entry.id))
        return cls(match.group("pool_id"), options or None)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Remove the chosen maps from the pool, re-render the editor."""
        from kingdoms.discord.maps_pool_flow import _guard_admin

        if not await _guard_admin(interaction):
            return
        chosen = [v for v in _selected_values(interaction) if v and v != "none"]
        if not chosen:
            await interaction.response.defer()
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        service = _games_wiring()
        if service is None:
            await interaction.followup.send((await reply(interaction, "ui.flows.wiring_unavailable")), ephemeral=True)
            return
        pool = await service.get_map_pool(self.pool_id)
        if pool is None:
            await interaction.followup.send((await reply(interaction, "ui.flows.pool_not_found")), ephemeral=True)
            return
        remaining = tuple(m for m in pool.map_ids if m not in set(chosen))
        try:
            await service.update_map_pool(self.pool_id, map_ids=remaining)
        except Exception as exc:
            logger.exception("GAMES ADMIN: pool map removal failed")
            await interaction.followup.send(f"Échec : {exc}", ephemeral=True)
            return
        try:
            from kingdoms.discord.pools_forum import sync_pools_forum

            if interaction.guild_id is not None:
                await sync_pools_forum(str(interaction.guild_id), interaction.client)
        except Exception:
            logger.warning("GAMES ADMIN: pools sync after removal failed — best-effort")
        await interaction.edit_original_response(
            view=await _pool_admin_menu(self.pool_id, pool.game_key)
        )
        await interaction.followup.send(f"{len(chosen)} map(s) retirée(s).", ephemeral=True)



class GamesMapsBackButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:mapsback:(?P<game_key>[a-z0-9_]+)",
):
    """Return from the maps sub-view to the game's sub-menu."""

    def __init__(self, game_key: str) -> None:
        self.game_key = game_key
        super().__init__(
            discord.ui.Button(
                label="Retour au jeu",
                style=discord.ButtonStyle.secondary,
                custom_id=f"{_NS}:mapsback:{game_key}"[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> GamesMapsBackButton:
        """Rebuild the item from the wire (game key from the custom_id)."""
        del interaction, item
        return cls(match.group("game_key"))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Re-render the game's sub-menu."""
        await interaction.response.send_message(view=await game_menu_view(self.game_key), ephemeral=True)


class GamesPoolsBackButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:poolsback:(?P<game_key>[a-z0-9_]+)",
):
    """Return from the pools sub-view to the game's sub-menu."""

    def __init__(self, game_key: str) -> None:
        self.game_key = game_key
        super().__init__(
            discord.ui.Button(
                label="Retour au jeu",
                style=discord.ButtonStyle.secondary,
                custom_id=f"{_NS}:poolsback:{game_key}"[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> GamesPoolsBackButton:
        """Rebuild the item from the wire (game key from the custom_id)."""
        del interaction, item
        return cls(match.group("game_key"))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Re-render the game's sub-menu."""
        await interaction.response.send_message(view=await game_menu_view(self.game_key), ephemeral=True)


class GamesPoolDuplicateButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:pools:duplicate:(?P<pool_id>.+)",
):
    """Duplicate the pool: the copy starts editable (that is its point)."""

    def __init__(self, pool_id: str) -> None:
        self.pool_id = pool_id
        super().__init__(
            discord.ui.Button(
                label="Duplicate",
                style=discord.ButtonStyle.primary,
                custom_id=f"{_NS}:pools:duplicate:{pool_id}"[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> GamesPoolDuplicateButton:
        """Rebuild the item from the wire (pool id from the custom_id)."""
        del interaction, item
        return cls(match.group("pool_id"))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Duplicate under a fresh name, then open the editable copy."""
        service = _games_wiring()
        if service is None:
            await interaction.response.send_message(
                await reply(interaction, "replies_shared.wiring_unavailable"), ephemeral=True
            )
            return
        pool = await service.get_map_pool(self.pool_id)
        if pool is None:
            await interaction.response.send_message(
                await reply(interaction, "replies_shared.pool_not_found"), ephemeral=True
            )
            return
        await interaction.response.send_modal(GamesPoolDuplicateModal(pool.id, pool.name))


class GamesPoolDuplicateModal(discord.ui.Modal):
    """The pool duplication form: the copy's name."""

    def __init__(self, pool_id: str, source_name: str) -> None:
        self.pool_id = pool_id
        super().__init__(title="Dupliquer le pool", timeout=None)
        self.name: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label="Copy name", max_length=64, required=True, default=f"{source_name} (copie)"
        )
        self.add_item(self.name)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Duplicate the pool, then open the editable copy's editor."""
        service = _games_wiring()
        if service is None:
            await interaction.response.send_message(
                await reply(interaction, "replies_shared.wiring_unavailable"), ephemeral=True
            )
            return
        guild_id = str(interaction.guild_id) if interaction.guild_id is not None else None
        try:
            copy = await service.duplicate_map_pool(self.pool_id, str(self.name.value).strip(), owner_guild_id=guild_id)
        except Exception:
            logger.exception("GAMES ADMIN: pool duplicate failed")
            await interaction.response.send_message("Duplication echouee (voir les logs).", ephemeral=True)
            return
        await interaction.response.edit_message(
            view=await _pool_admin_menu(copy.id, copy.game_key)
        )
        await interaction.followup.send(f"Pool duplique en **{copy.name}** (editable).", ephemeral=True)


class GamesPoolSendButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:pools:send:(?P<pool_id>.+)",
):
    """Send the pool to another guild: the received copy is locked."""

    def __init__(self, pool_id: str) -> None:
        self.pool_id = pool_id
        super().__init__(
            discord.ui.Button(
                label="Envoyer à un serveur",
                style=discord.ButtonStyle.secondary,
                custom_id=f"{_NS}:pools:send:{pool_id}"[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> GamesPoolSendButton:
        """Rebuild the item from the wire (pool id from the custom_id)."""
        del interaction, item
        return cls(match.group("pool_id"))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the send modal after resolving the pool."""
        service = _games_wiring()
        if service is None:
            await interaction.response.send_message(
                await reply(interaction, "replies_shared.wiring_unavailable"), ephemeral=True
            )
            return
        pool = await service.get_map_pool(self.pool_id)
        if pool is None:
            await interaction.response.send_message(
                await reply(interaction, "replies_shared.pool_not_found"), ephemeral=True
            )
            return
        await interaction.response.send_modal(GamesPoolSendModal(pool.id, pool.name))


class GamesPoolSendModal(discord.ui.Modal):
    """The pool send form: target guild id + the copy's name."""

    def __init__(self, pool_id: str, pool_name: str) -> None:
        self.pool_id = pool_id
        super().__init__(title="Envoyer le pool", timeout=None)
        self.guild_id: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label="Target guild ID", max_length=25, required=True
        )
        self.name: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label="Received pool name", max_length=64, required=True, default=pool_name
        )
        self.add_item(self.guild_id)
        self.add_item(self.name)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Send the locked copy to the target guild, confirm."""
        service = _games_wiring()
        if service is None:
            await interaction.response.send_message(
                await reply(interaction, "replies_shared.wiring_unavailable"), ephemeral=True
            )
            return
        target = str(self.guild_id.value or "").strip()
        if not target.isdigit():
            await interaction.response.send_message("L'ID du serveur doit être numérique.", ephemeral=True)
            return
        try:
            copy = await service.send_map_pool_to_guild(self.pool_id, target, str(self.name.value).strip())
        except Exception:
            logger.exception("GAMES ADMIN: pool send failed")
            await interaction.response.send_message("Envoi echoue (nom deja pris ? voir les logs).", ephemeral=True)
            return
        await interaction.response.send_message(
            f"Pool **{copy.name}** envoyé au serveur {target} — il y sera verrouillé (non modifiable).",
            ephemeral=True,
        )


class GamesMapImportButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:maps:import:(?P<scope>global|guild):(?P<game_key>[a-z0-9_]+)",
):
    """Import one public map from Liquipedia by name."""

    def __init__(self, game_key: str) -> None:
        self.game_key = game_key
        super().__init__(
            discord.ui.Button(
                label="Importer une map",
                emoji="\U0001f310",
                style=discord.ButtonStyle.secondary,
                custom_id=f"{_NS}:maps:import:guild:{game_key}"[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> GamesMapImportButton:
        """Rebuild the item from the wire (game key from the custom_id)."""
        del interaction, item
        return cls(match.group("game_key"))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Guard, then open the Liquipedia import modal."""
        from kingdoms.discord.maps_pool_flow import _guard_admin

        if not await _guard_admin(interaction):
            return
        await interaction.response.send_modal(GamesMapImportModal(self.game_key))


def _map_summary_from_seed(seed: Any) -> str:
    """Render the imported map's content: Liquipedia description + traits.

    The infobox traits (type de terrain, nomade, murs, town centers)
    join the description as a compact line — the post shows them in
    the subtitle and the content store keeps them queryable.
    """
    bits: list[str] = []
    if seed.map_type:
        bits.append(f"Type : {seed.map_type}")
    if seed.nomad:
        bits.append("Nomade" if seed.nomad.strip().lower() in ("yes", "true") else f"Nomade : {seed.nomad}")
    if seed.walls:
        bits.append(f"Fortifications : {seed.walls}")
    if seed.tc and seed.tc.isdigit() and int(seed.tc) > 1:
        bits.append(f"Town centers : {seed.tc}")
    traits = " · ".join(bits)
    description = (seed.description or "").strip()
    if traits and description:
        return f"**{traits}**\n\n{description}"
    return traits or description


async def _finish_map_import(
    interaction: discord.Interaction,
    service: Any,
    game_key: str,
    entry: Any,
    seed: Any,
) -> None:
    """Store the imported map's content and sync its forum post (#259).

    Transactional tail: any failure after the record landed rolls the
    record back — no orphaned "name taken" residue. When the rollback
    itself fails, the popup says the map is orphaned and how to clean
    it (delete from the Maps menu, no forum thread needed).
    """
    try:
        from kingdoms.discord.content_posts import content_service

        content = content_service()
        if content is not None:
            await content.store(
                {
                    "entity_id": entry.id,
                    "locale": "en",
                    "name": entry.name,
                    "summary": seed.description,
                    "source_url": seed.resource_url,
                    "image_url": seed.image_url,
                    "icon_url": seed.icon_url,
                    "walls": seed.walls,
                    "nomad": seed.nomad,
                    "tc": seed.tc,
                    "provider": "liquipedia",
                    "tags": list(seed.tags or ()),
                }
            )
        from kingdoms.discord.maps_forum import sync_maps_forum

        if interaction.guild_id is not None:
            await sync_maps_forum(str(interaction.guild_id), game_key, interaction.client)
    except Exception as exc:
        logger.exception("GAMES ADMIN: map import tail failed — rolling back %s", entry.id)
        rolled_back = False
        try:
            await service.delete_map(entry.id)
            rolled_back = True
        except Exception:
            logger.exception("GAMES ADMIN: rollback failed for %s", entry.id)
        if rolled_back:
            await interaction.followup.send(
                f"Import échoué à l'étape « contenu/forum » ({exc}) — la map a été supprimée, "
                "réessaie.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                f"Import échoué à l'étape « contenu/forum » ({exc}) et le nettoyage a échoué : "
                f"la map **{entry.name}** (`{entry.id}`) est orpheline — supprime-la depuis le "
                "menu Maps puis réessaie.",
                ephemeral=True,
            )
        return
    fresh = await service.get_map(entry.id)
    post_link = ""
    if fresh is not None and fresh.forum_message_id and interaction.guild_id is not None:
        post_link = (
            f" — son post : https://discord.com/channels/{interaction.guild_id}/"
            f"{fresh.forum_message_id}/{fresh.forum_message_id}"
        )
    await interaction.followup.send(
        f"Map **{entry.name}** importée{post_link or ' — son post est dans le forum maps.'}",
        ephemeral=True,
    )


class GamesMapImportModal(discord.ui.Modal):
    """The Liquipedia map import form: the map's page name."""

    def __init__(self, game_key: str) -> None:
        self.game_key = game_key
        super().__init__(title="Importer une map (Liquipedia)", timeout=None)
        self.name: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label="Map name (Liquipedia page)", max_length=64, required=True
        )
        self.add_item(self.name)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Import the map by name; Liquipedia fills the public content."""
        service = _games_wiring()
        if service is None:
            await interaction.response.send_message(
                await reply(interaction, "replies_shared.wiring_unavailable"), ephemeral=True
            )
            return
        name = str(self.name.value or "").strip()
        if not name:
            await interaction.response.send_message(
                await reply(interaction, "replies_shared.name_required"), ephemeral=True
            )
            return
        # Ack FIRST: the Liquipedia fetch is rate-limited (>= 2 s per
        # request, 3-4 requests per map) — way past Discord's 3-second
        # modal window; the ack-less import raised in the popup while
        # the map was still created, leaving "name already taken" on
        # retry.
        await interaction.response.defer(ephemeral=True, thinking=True)
        from kingdoms.mapsdata.seed import LiquipediaUnconfiguredError, fetch_map_seed
        try:
            seed = await fetch_map_seed(name)
        except LiquipediaUnconfiguredError:
            await interaction.followup.send(
                await reply(interaction, "replies_shared.liquipedia_unconfigured"),
                ephemeral=True,
            )
            return
        if seed is None:
            await interaction.followup.send(
                f'Aucune page Liquipedia pour "{name}" \u2014 cr\u00e9e la map localement.',
                ephemeral=True,
            )
            return
        from kingdoms.core.services.game_data import NameTakenError

        try:
            entry = await service.create_map(
                self.game_key,
                seed.name,
                filename=seed.name.replace(" ", "_").lower(),
                description=seed.description,
                resource_url=seed.resource_url,
                owner_guild_id=None,
                map_type=seed.map_type,
            )
        except NameTakenError as error:
            await interaction.followup.send(
                f"Import refusé — {error}. Supprime ou archive la map existante d'abord.",
                ephemeral=True,
            )
            return
        except Exception as exc:
            logger.exception("GAMES ADMIN: map creation failed")
            await interaction.followup.send(
                f"Import échoué à l'étape « création » : {exc}", ephemeral=True
            )
            return
        await _finish_map_import(interaction, service, self.game_key, entry, seed)


async def _bulk_import_maps(service: Any, game_key: str, parsed: list[Any]) -> tuple[list[Any], str]:
    """Import the missing maps transactionally; (created, failure).

    Existing non-archived maps are skipped (re-import = no-op); any
    failure returns the created-so-far entries with the reason — the
    caller rolls them back (#259 semantics for the bulk path).
    """
    from kingdoms.mapsdata.seed import fetch_map_seed

    created: list[Any] = []
    for p in parsed:
        try:
            from kingdoms.core.ids import map_entry_id

            existing = await service.get_map(map_entry_id(game_key, p.page_name, None))
            if existing is not None and existing.archived_at is None:
                continue
            seed = await fetch_map_seed(p.page_name)
            if seed is None:
                return created, f"Aucune page Liquipedia pour « {p.page_name} »."
            entry = await service.create_map(
                game_key,
                seed.name,
                filename=seed.name.replace(" ", "_").lower(),
                description=seed.description,
                resource_url=seed.resource_url,
                owner_guild_id=None,
                map_type=seed.map_type,
            )
            from kingdoms.discord.content_posts import content_service

            content = content_service()
            if content is not None:
                await content.store(
                    {
                        "entity_id": entry.id,
                        "locale": "en",
                        "name": entry.name,
                        "summary": seed.description,
                        "source_url": seed.resource_url,
                        "image_url": seed.image_url,
                        "icon_url": seed.icon_url,
                        "walls": seed.walls,
                        "nomad": seed.nomad,
                        "tc": seed.tc,
                        "provider": "liquipedia",
                        "tags": list(seed.tags or ()),
                    }
                )
            created.append(entry)
        except Exception as exc:
            logger.exception("GAMES ADMIN: bulk import failed on %s", p.page_name)
            return created, f"échec sur « {p.page_name} » ({exc})."
    return created, ""


async def _finish_bulk_import(
    interaction: discord.Interaction,
    service: Any,
    created: list[Any],
    pool_id: str,
) -> None:
    """Associate the batch to its pool, sync the forum, confirm."""
    if pool_id:
        try:
            pool = await service.get_map_pool(pool_id)
            if pool is not None:
                ids = set(pool.map_ids)
                ids.update(e.id for e in created)
                await service.update_map_pool(pool_id, map_ids=tuple(sorted(ids)))
        except Exception:
            logger.exception("GAMES ADMIN: bulk pool association failed")
            await interaction.followup.send(
                f"{len(created)} maps importées mais l'association au pool a échoué (voir les logs).",
                ephemeral=True,
            )
            return
    try:
        from kingdoms.discord.maps_forum import sync_maps_forum

        if interaction.guild_id is not None:
            game = str(created[0].game_key) if created else "aoe2"
            await sync_maps_forum(str(interaction.guild_id), game, interaction.client)
    except Exception:
        logger.warning("GAMES ADMIN: forum sync after bulk import failed — best-effort")
    target = f" et associées au pool `{pool_id}`" if pool_id else ""
    await interaction.followup.send(
        f"{len(created)} maps importées{target} — les posts arrivent dans le forum maps.",
        ephemeral=True,
    )


class GamesBulkImportButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:maps:bulkimport:(?P<game_key>[a-z0-9_]+)",
):
    """Open the bulk map-import modal (id / slug / URL per line)."""

    def __init__(self, game_key: str) -> None:
        self.game_key = game_key
        super().__init__(
            discord.ui.Button(
                label="Importer des maps (liste)",
                emoji="\U0001f4da",
                style=discord.ButtonStyle.secondary,
                custom_id=f"{_NS}:maps:bulkimport:{game_key}"[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> GamesBulkImportButton:
        """Rebuild from the wire (game key from the custom_id)."""
        del interaction, item
        return cls(match.group("game_key"))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Guard, then open the bulk import modal."""
        from kingdoms.discord.maps_pool_flow import _guard_admin

        if not await _guard_admin(interaction):
            return
        await interaction.response.send_modal(GamesBulkImportModal(self.game_key))


class GamesBulkImportModal(discord.ui.Modal):
    """The bulk Liquipedia import: one map per line (id / slug / URL).

    Missing maps are imported from Liquipedia and (optionally) all
    lines are associated to a target pool. Any failure rolls the whole
    batch back — the catalog never keeps a half-imported state (#259).
    """

    def __init__(self, game_key: str, pool_id: str = "") -> None:
        self.game_key = game_key
        self.pool_id = pool_id
        title = "Importer des maps (liste)"
        super().__init__(title=title, timeout=None)
        self.lines: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label="Maps (id, nom ou URL, une par ligne)",
            style=discord.TextStyle.paragraph,
            max_length=3800,
            required=True,
        )
        self.add_item(self.lines)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Guard, parse, import the batch transactionally, report."""
        from kingdoms.discord.maps_pool_flow import _guard_admin

        if not await _guard_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        from kingdoms.mapsdata.bulk_import import parse_import_line

        parsed = [parse_import_line(item) for item in str(self.lines.value or "").splitlines()]
        parsed = [p for p in parsed if p.raw]
        errors = [p.error for p in parsed if p.error]
        if errors:
            await interaction.followup.send(
                "Import refusé — lignes invalides :\n" + "\n".join(f"- {e}" for e in errors[:10]),
                ephemeral=True,
            )
            return
        if not parsed:
            await interaction.followup.send("Aucune map dans la liste.", ephemeral=True)
            return
        service = _games_wiring()
        if service is None:
            await interaction.followup.send((await reply(interaction, "ui.flows.wiring_unavailable")), ephemeral=True)
            return

        created, failure = await _bulk_import_maps(service, self.game_key, parsed)
        if failure:
            logger.exception("GAMES ADMIN: bulk import failed — rolling back %d maps", len(created))
            rolled = 0
            for entry in created:
                try:
                    await service.delete_map(entry.id)
                    rolled += 1
                except Exception:
                    logger.exception("GAMES ADMIN: bulk rollback failed for %s", entry.id)
            await interaction.followup.send(
                f"Import refusé — {failure} "
                f"{rolled}/{len(created)} maps créées ont été supprimées ; corrige la liste et réessaie.",
                ephemeral=True,
            )
            return
        await _finish_bulk_import(interaction, service, created, self.pool_id)



class GamesPoolCopyButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:pools:copy:(?P<game_key>[a-z0-9_]+)",
):
    """Copy an existing map pool by its id (a real copy, own id)."""

    def __init__(self, game_key: str) -> None:
        self.game_key = game_key
        super().__init__(
            discord.ui.Button(
                label="Copier un map pool",
                emoji="\U0001f4cb",
                style=discord.ButtonStyle.secondary,
                custom_id=f"{_NS}:pools:copy:{game_key}"[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> GamesPoolCopyButton:
        """Rebuild the item from the wire (game key from the custom_id)."""
        del interaction, item
        return cls(match.group("game_key"))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Guard, then open the pool copy modal."""
        from kingdoms.discord.maps_pool_flow import _guard_admin

        if not await _guard_admin(interaction):
            return
        await interaction.response.send_modal(GamesPoolCopyModal(self.game_key))


class GamesPoolCopyModal(discord.ui.Modal):
    """The pool copy form: the source pool's id + the copy's name.

    The copy is a real copy (duplicate_map_pool): its own stable id
    scoped to this guild, its own map list, editable — the source
    stays untouched. The admin finds the source pool's id in its
    post's footer (pool:<guild_id>:<slug>).
    """

    def __init__(self, game_key: str) -> None:
        self.game_key = game_key
        super().__init__(title="Copier un map pool", timeout=None)
        self.source_id: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label="Id du pool à copier (footer du post, ex. pool:public:s1_r1)",
            max_length=100,
            required=True,
        )
        self.new_name: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label="Nom de la copie",
            max_length=64,
            required=True,
        )
        self.add_item(self.source_id)
        self.add_item(self.new_name)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Copy the pool by id into this guild (guard, defer, sync, link)."""
        from kingdoms.discord.maps_pool_flow import _guard_admin

        if not await _guard_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        service = _games_wiring()
        if service is None:
            await interaction.followup.send((await reply(interaction, "ui.flows.wiring_unavailable")), ephemeral=True)
            return
        source_id = str(self.source_id.value or "").strip()
        source = await service.get_map_pool(source_id) if source_id else None
        if source is None:
            await interaction.followup.send(
                f"Pool introuvable : `{source_id}` — l'id est dans le footer du post du pool.",
                ephemeral=True,
            )
            return
        guild_id = str(interaction.guild_id) if interaction.guild_id is not None else None
        try:
            copy = await service.duplicate_map_pool(
                source.id, str(self.new_name.value or "").strip(), owner_guild_id=guild_id
            )
        except Exception as exc:
            logger.exception("GAMES ADMIN: pool copy failed")
            await interaction.followup.send(f"Échec de la copie : {exc}", ephemeral=True)
            return
        try:
            from kingdoms.discord.pools_forum import sync_pools_forum

            if interaction.guild_id is not None:
                await sync_pools_forum(str(interaction.guild_id), interaction.client)
        except Exception:
            logger.warning("GAMES ADMIN: pools forum sync after copy failed — best-effort")
        await interaction.followup.send(
            f"Pool **{copy.name}** copié (`{copy.id}`) — {len(copy.map_ids)} maps, "
            "son post est dans le forum des map pools.",
            ephemeral=True,
        )
        await _refresh_pins_after_mutation(interaction)


class GamesPoolImportModal(discord.ui.Modal):
    """The pool import form: the source pool's unique id."""

    def __init__(self, game_key: str) -> None:
        self.game_key = game_key
        super().__init__(title="Importer un map pool", timeout=None)
        self.pool_id: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label="Source pool id (e.g. map_pool:aoe2:ladder_cf_s1_r1)",
            max_length=100,
            required=True,
        )
        self.add_item(self.pool_id)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Import the pool by id; only published pools are importable."""
        service = _games_wiring()
        if service is None:
            await interaction.response.send_message(
                await reply(interaction, "replies_shared.wiring_unavailable"), ephemeral=True
            )
            return
        source_id = str(self.pool_id.value or "").strip()
        source = await service.get_map_pool(source_id) if source_id else None
        if source is None:
            await interaction.response.send_message(
                await reply(interaction, "replies_shared.pool_not_found"), ephemeral=True
            )
            return
        guild_id = str(interaction.guild_id) if interaction.guild_id is not None else None
        if guild_id is not None and source.owner_guild_id == guild_id:
            await interaction.response.send_message("Ce pool appartient déjà à ce serveur.", ephemeral=True)
            return
        if not source.is_public:
            await interaction.response.send_message("Seuls les pools publies peuvent etre importes.", ephemeral=True)
            return
        try:
            copy = await service.send_map_pool_to_guild(source.id, guild_id or "unknown", f"{source.name} (importe)")
        except Exception:
            logger.exception("GAMES ADMIN: pool import failed")
            await interaction.response.send_message(
                await reply(interaction, "replies_shared.import_failed"), ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"Pool **{copy.name}** importe (verrouille, non modifiable).", ephemeral=True
        )


def register_games_admin_section() -> None:
    """Register the games section into the /admin panel (idempotent)."""
    register_admin_mod_section(
        AdminModSection(
            mod=MOD_KEY,
            label="Games",
            description="Catalogues de jeux : maps, map pools",
            entry=games_admin_entry,
            core=True,
        )
    )


def register_games_admin_items(bot: discord.Client) -> None:
    """Register the section's DynamicItems (called at every startup)."""
    bot.add_dynamic_items(
        GamesBackButton,
        GamesGameSelect,
        GamesMapsButton,
        GamesPoolsButton,
        GamesMapsBackButton,
        GamesPoolsBackButton,
        GamesMapArchiveSelect,
        GamesMapDeleteSelect,
        GamesMapCreateButton,
        GamesPoolCreateButton,
        GamesPoolRenameButton,
        GamesPoolToDraftButton,
        GamesPoolArchiveButton,
        GamesPoolMapToggle,
        GamesPoolMapPageSelect,
        GamesPoolAddByIdButton,
        GamesPoolMapRemoveSelect,
        GamesPoolDuplicateButton,
        GamesPoolSendButton,
        GamesMapImportButton,
        GamesBulkImportButton,
        GamesPoolCopyButton,
        GamesGrantedSelect,
        GamesGrantedReopenButton,
        GamesCivsSectionButton,
        GamesMapsSectionButton,
        GamesPoolsSectionButton,
        GamesMapsManageButton,
        GamesPoolsManageButton,
        GamesPoolDeleteSelect,
        GamesPoolPublishButton,
        GamesPoolHardDeleteButton,
    )
