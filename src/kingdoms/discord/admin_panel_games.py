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
from kingdoms.discord.view_origin import from_pin

logger = logging.getLogger("kingdoms.games.admin_panel")

MOD_KEY = "games"
_NS = "admin:pin:modgames"
GAME_KEY = "aoe2"  # default context; the picker may switch it per view


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
    """Read a select interaction's chosen values (payload-shape proof)."""
    data = interaction.data
    raw = getattr(data, "values", None) if data is not None else None
    if raw is None and isinstance(data, dict):
        raw = data.get("values")
    return list(raw) if isinstance(raw, (list, tuple)) else []


async def games_admin_entry(interaction: discord.Interaction) -> discord.ui.LayoutView:
    """Render the games admin section: game picker + per-game actions."""
    del interaction
    service = _games_wiring()
    view = discord.ui.LayoutView(timeout=None)
    blocks: list[Any] = [discord.ui.TextDisplay("# Games admin")]
    if service is None:
        blocks.append(discord.ui.TextDisplay("Wiring unavailable: Mongo is not configured."))
        view.add_item(discord.ui.Container(*blocks, accent_colour=discord.Colour(0x5865F2)))
        view.add_item(_back_row())
        return view
    from kingdoms.discord.wiring import granted_game_keys

    keys = list(await granted_game_keys("", tuple(await service.list_game_keys())))
    if not keys:
        blocks.append(discord.ui.TextDisplay("_No game known — seed a catalog._"))
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
                placeholder="Manage a game...",
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
    """One game's sub-menu: snapshot, forum links, civs reload, maps/pools.

    A pin-opened view carries no back button (the pin stays under the
    ephemeral); a command-opened one keeps it for the walk back.
    """
    service = _games_wiring()
    view = discord.ui.LayoutView(timeout=None)
    blocks: list[Any] = [discord.ui.TextDisplay(f"# {game_key.capitalize()} \u2014 Admin")]
    if service is None:
        blocks.append(discord.ui.TextDisplay("Wiring indisponible."))
        view.add_item(discord.ui.Container(*blocks))
        if not from_pin:
            view.add_item(_back_row())
        return view
    maps = await service.list_maps(game_key)
    pools = await service.list_map_pools(game_key)
    active_maps = [m for m in maps if m.archived_at is None]
    blocks.extend(
        [
            discord.ui.Separator(),
            discord.ui.TextDisplay(f"**Maps actives** : {len(active_maps)} \u2014 **Pools** : {len(pools)}"),
        ]
    )
    links = await _forum_links(game_key, guild_id)
    if links:
        blocks.append(discord.ui.TextDisplay(links))
    view.add_item(discord.ui.Container(*blocks, accent_colour=discord.Colour(0x5865F2)))
    actions: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    actions.add_item(GamesMapsButton(game_key))
    actions.add_item(GamesPoolsButton(game_key))
    view.add_item(actions)
    imports: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    imports.add_item(GamesMapImportButton(game_key))
    imports.add_item(GamesPoolImportButton(game_key))
    view.add_item(imports)
    content_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    content_row.add_item(GamesCivsReloadButton(game_key))
    view.add_item(content_row)
    if not from_pin:
        view.add_item(_back_row())
    return view


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


class GamesCivsReloadButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:civs:reload",
):
    """Reload the civs content from the aoe2techtree dataset (admins)."""

    def __init__(self, game_key: str) -> None:
        self.game_key = game_key
        super().__init__(
            discord.ui.Button(
                label="Rafraichir les civs",
                emoji="\U0001f504",
                style=discord.ButtonStyle.secondary,
                custom_id=f"{_NS}:civs:reload"[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> GamesCivsReloadButton:
        """Rebuild the item from the wire."""
        del interaction, item, match
        return cls("aoe2")

    async def callback(self, interaction: discord.Interaction) -> None:
        """Run the content refresh, report the counts."""
        from kingdoms.discord.maps_pool_flow import _guard_admin

        if not await _guard_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            from kingdoms.core.games.aoe2.content_refresh import refresh_aoe2_content

            counts = await refresh_aoe2_content()
        except Exception:
            logger.exception("GAMES ADMIN: civs refresh failed")
            await interaction.followup.send("Refresh echoue (voir les logs).", ephemeral=True)
            return
        await interaction.followup.send(
            f"Contenu rafraichi : {counts.get('total_factions', 0)} civs, "
            f"{counts.get('content_docs', 0)} documents, "
            f"{counts.get('maps_enriched', 0)} maps enrichies.",
            ephemeral=True,
        )


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
        chosen = (_selected_values(interaction) or [""])[0]
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
            discord.ui.Button(label="Maps", style=discord.ButtonStyle.primary, custom_id=f"{_NS}:maps:{game_key}"[:100])
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
        """Render the maps sub-view."""
        await interaction.response.edit_message(
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
                label="Map pools", style=discord.ButtonStyle.primary, custom_id=f"{_NS}:pools:{game_key}"[:100]
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
        """Render the pools sub-view."""
        await interaction.response.edit_message(
            view=await pools_admin_view(self.game_key, from_pin=from_pin(interaction))
        )


async def maps_admin_view(game_key: str, from_pin: bool = False) -> discord.ui.LayoutView:
    """Render the game's maps sub-view: the catalog with archive toggles."""
    service = _games_wiring()
    view = discord.ui.LayoutView(timeout=None)
    blocks: list[Any] = [discord.ui.TextDisplay(f"# Maps ({game_key})")]
    if service is None:
        blocks.append(discord.ui.TextDisplay("Wiring unavailable."))
        view.add_item(discord.ui.Container(*blocks))
        view.add_item(_back_row())
        return view
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
    action_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    action_row.add_item(GamesMapCreateButton(game_key))
    action_row.add_item(GamesMapCreateButton(game_key, global_scope=True))
    view.add_item(action_row)
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
        label = "+ Map globale" if global_scope else "+ Map"
        custom_id = f"{_NS}:maps:create:{'global' if global_scope else 'guild'}:{game_key}"
        super().__init__(discord.ui.Button(label=label, style=discord.ButtonStyle.success, custom_id=custom_id[:100]))

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
        super().__init__(title="Create a global map" if global_scope else "Creer une map", timeout=None)
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
            from kingdoms.mapsdata.seed import fetch_map_seed

            seed = await fetch_map_seed(name)
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
                options=options or [discord.SelectOption(label="No map", value="none")],
                placeholder="Enable / disable a map...",
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


async def pools_admin_view(game_key: str, from_pin: bool = False) -> discord.ui.LayoutView:
    """Render the game's map pools sub-view: list + CRUD actions."""
    service = _games_wiring()
    view = discord.ui.LayoutView(timeout=None)
    blocks: list[Any] = [discord.ui.TextDisplay(f"# Map pools ({game_key})")]
    if service is None:
        blocks.append(discord.ui.TextDisplay("Wiring unavailable."))
        view.add_item(discord.ui.Container(*blocks))
        view.add_item(_back_row())
        return view
    pools = await service.list_map_pools(game_key)
    if not pools:
        blocks.append(discord.ui.TextDisplay("_No map pool._"))
    else:
        lines = [f"- {p.name} ({len(p.map_ids)} maps)" for p in pools[:15]]
        blocks.append(discord.ui.TextDisplay("\n".join(lines)))
    view.add_item(discord.ui.Container(*blocks))
    select_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    select_row.add_item(GamesPoolEditSelect(game_key))
    view.add_item(select_row)
    action_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    action_row.add_item(GamesPoolCreateButton(game_key))
    view.add_item(action_row)
    if not from_pin:
        view.add_item(GamesPoolsBackButton(game_key))
    return view


class GamesPoolCreateButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:pools:create:(?P<game_key>[a-z0-9_]+)",
):
    """Open the pool-creation modal (name)."""

    def __init__(self, game_key: str) -> None:
        self.game_key = game_key
        super().__init__(
            discord.ui.Button(
                label="+ Pool", style=discord.ButtonStyle.success, custom_id=f"{_NS}:pools:create:{game_key}"[:100]
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
        super().__init__(title="Create a map pool", timeout=None)
        self.name: discord.ui.TextInput[Any] = discord.ui.TextInput(label="Pool name", max_length=64, required=True)
        self.map_name: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label="First map (name, optional)", max_length=64, required=False
        )
        self.add_item(self.name)
        self.add_item(self.map_name)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Create the pool (with the optional first map), confirm."""
        service = _games_wiring()
        if service is None:
            await interaction.response.send_message(
                await reply(interaction, "replies_shared.wiring_unavailable"), ephemeral=True
            )
            return
        map_ids: tuple[str, ...] = ()
        map_name = str(self.map_name.value or "").strip()
        if map_name:
            from kingdoms.core.services.game_data import MAPS_COLLECTION

            doc = await service._db.find_by_name(MAPS_COLLECTION, self.game_key, map_name)
            if doc is None:
                await interaction.response.send_message(f"Map inconnue : {map_name}.", ephemeral=True)
                return
            map_ids = (str(doc["_id"]),)
        try:
            pool = await service.create_map_pool(self.game_key, str(self.name.value).strip(), map_ids=map_ids)
        except Exception:
            logger.exception("GAMES ADMIN: pool creation failed")
            await interaction.response.send_message(
                await reply(interaction, "replies_shared.creation_failed"), ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"Pool **{pool.name}** créé - ajoute ses maps via la liste.", ephemeral=True
        )


class GamesPoolEditSelect(
    discord.ui.DynamicItem[discord.ui.Select[Any]],
    template=rf"{_NS}:pools:edit:(?P<game_key>[a-z0-9_]+)",
):
    """Pick a pool to edit; opens the pool's editor view."""

    def __init__(self, game_key: str, options: list[discord.SelectOption] | None = None) -> None:
        self.game_key = game_key
        super().__init__(
            discord.ui.Select(
                custom_id=f"{_NS}:pools:edit:{game_key}"[:100],
                options=options or [discord.SelectOption(label="No map pool", value="none")],
                placeholder="Edit a pool...",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> GamesPoolEditSelect:
        """Rebuild the select's options at click time."""
        service = _games_wiring()
        game_key = match.group("game_key")
        options: list[discord.SelectOption] = []
        if service is not None:
            pools = await service.list_map_pools(game_key)
            options = [discord.SelectOption(label=f"{p.name} ({len(p.map_ids)} maps)", value=p.id) for p in pools[:25]]
        return cls(game_key, options)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the chosen pool's editor view."""
        chosen = (_selected_values(interaction) or [""])[0]
        if not chosen or chosen == "none":
            await interaction.response.defer()
            return
        await interaction.response.edit_message(
            view=await pool_editor_view(chosen, self.game_key, from_pin=from_pin(interaction))
        )


async def pool_editor_view(pool_id: str, game_key: str, page: int = 0, from_pin: bool = False) -> discord.ui.LayoutView:
    """One pool's editor: rename, add/remove maps (paged, 25 per select), archive."""
    service = _games_wiring()
    view = discord.ui.LayoutView(timeout=None)
    blocks: list[Any] = [discord.ui.TextDisplay("# Editer le pool")]
    if service is None:
        blocks.append(discord.ui.TextDisplay("Wiring unavailable."))
        view.add_item(discord.ui.Container(*blocks))
        view.add_item(_back_row())
        return view
    pool = await service.get_map_pool(pool_id)
    if pool is None:
        blocks.append(discord.ui.TextDisplay("Pool introuvable."))
        view.add_item(discord.ui.Container(*blocks))
        view.add_item(_back_row())
        return view
    maps = [m for m in await service.list_maps(pool.game_key) if m.archived_at is None]
    names = {m.id: m.name for m in maps}
    from kingdoms.core.ids import footer
    from kingdoms.core.services.game_data import POOL_STATE_LABELS

    state = POOL_STATE_LABELS.get(getattr(pool, "state", "draft"), "created")
    lines = [f"**{pool.name}** - {len(pool.map_ids)} maps - {state}", footer(pool.id)]
    for map_id in pool.map_ids:
        lines.append(f"- {names.get(map_id, map_id)}")
    if not pool.map_ids:
        lines.append("_Aucune map._")
    blocks.append(discord.ui.TextDisplay("\n".join(lines)))
    view.add_item(discord.ui.Container(*blocks))
    # Discord caps a select at 25 options: with 50+ maps the picker pages
    # through the catalog; the page rides the toggle's custom_id so the
    # re-render after a toggle stays on the same page.
    page_count = max(1, -(-len(maps) // PAGE_SIZE))
    page = max(0, min(page, page_count - 1))
    select_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    if pool.edition_mode:
        select_row.add_item(GamesPoolMapToggle(pool_id, page, _page_options(pool, maps, page)))
        view.add_item(select_row)
    if page_count > 1:
        page_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
        page_row.add_item(GamesPoolMapPageSelect(pool_id, page_count, page))
        view.add_item(page_row)
    action_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    action_row.add_item(GamesPoolDuplicateButton(pool_id))
    if pool.edition_mode:
        action_row.add_item(GamesPoolRenameButton(pool_id))
        action_row.add_item(GamesPoolArchiveButton(pool_id))
    view.add_item(action_row)
    send_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    send_row.add_item(GamesPoolSendButton(pool_id))
    view.add_item(send_row)
    if not from_pin:
        back_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
        back_row.add_item(GamesPoolsBackButton(pool.game_key))
        view.add_item(back_row)
    return view


class GamesPoolRenameButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:pools:rename:(?P<pool_id>.+)",
):
    """Open the rename modal; the pool id rides the custom_id."""

    def __init__(self, pool_id: str) -> None:
        self.pool_id = pool_id
        super().__init__(
            discord.ui.Button(
                label="Rename", style=discord.ButtonStyle.primary, custom_id=f"{_NS}:pools:rename:{pool_id}"[:100]
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
        super().__init__(title="Rename the pool", timeout=None)
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
        await interaction.response.edit_message(
            view=await pool_editor_view(pool.id, pool.game_key, from_pin=from_pin(interaction))
        )
        await interaction.followup.send(f"Pool renomme **{pool.name}**.", ephemeral=True)


class GamesPoolArchiveButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:pools:archive:(?P<pool_id>[^:]+)",
):
    """Archive (delete) the pool, unless it is the active pool of a mod."""

    def __init__(self, pool_id: str) -> None:
        self.pool_id = pool_id
        super().__init__(
            discord.ui.Button(
                label="Delete", style=discord.ButtonStyle.danger, custom_id=f"{_NS}:pools:archive:{pool_id}"[:100]
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
        try:
            await service.archive_map_pool(self.pool_id)
        except Exception:
            logger.exception("GAMES ADMIN: pool archive failed")
            await interaction.response.send_message("Archivage echoue (pool actif ? voir les logs).", ephemeral=True)
            return
        await interaction.response.edit_message(view=await pools_admin_view(pool.game_key))
        await interaction.followup.send("Pool supprime (archive).", ephemeral=True)


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
        page = int((self.item.values or ["0"])[0])
        await interaction.response.edit_message(
            view=await pool_editor_view(self.pool_id, pool.game_key, page, from_pin=from_pin(interaction))
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
                options=options or [discord.SelectOption(label="No map", value="none")],
                placeholder="Add / remove a map...",
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
            view=await pool_editor_view(self.pool_id, pool.game_key, self.page, from_pin=from_pin(interaction))
        )
        await interaction.followup.send("Map retiree." if removed else "Map ajoutee.", ephemeral=True)


class GamesMapsBackButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:mapsback:(?P<game_key>[a-z0-9_]+)",
):
    """Return from the maps sub-view to the game's sub-menu."""

    def __init__(self, game_key: str) -> None:
        self.game_key = game_key
        super().__init__(
            discord.ui.Button(
                label="Back to the game",
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
                label="Back to the game",
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
    template=rf"{_NS}:pools:duplicate:(?P<pool_id>[^:]+)",
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
        super().__init__(title="Duplicate the pool", timeout=None)
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
            view=await pool_editor_view(copy.id, copy.game_key, from_pin=from_pin(interaction))
        )
        await interaction.followup.send(f"Pool duplique en **{copy.name}** (editable).", ephemeral=True)


class GamesPoolSendButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:pools:send:(?P<pool_id>[^:]+)",
):
    """Send the pool to another guild: the received copy is locked."""

    def __init__(self, pool_id: str) -> None:
        self.pool_id = pool_id
        super().__init__(
            discord.ui.Button(
                label="Send to a guild",
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
        super().__init__(title="Send the pool", timeout=None)
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
            await interaction.response.send_message("L'ID de guilde doit etre numerique.", ephemeral=True)
            return
        try:
            copy = await service.send_map_pool_to_guild(self.pool_id, target, str(self.name.value).strip())
        except Exception:
            logger.exception("GAMES ADMIN: pool send failed")
            await interaction.response.send_message("Envoi echoue (nom deja pris ? voir les logs).", ephemeral=True)
            return
        await interaction.response.send_message(
            f"Pool **{copy.name}** envoye a la guilde {target} - il y sera verrouille (non modifiable).",
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
                label="Import a map",
                emoji="\U0001f4e5",
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


class GamesMapImportModal(discord.ui.Modal):
    """The Liquipedia map import form: the map's page name."""

    def __init__(self, game_key: str) -> None:
        self.game_key = game_key
        super().__init__(title="Import a map (Liquipedia)", timeout=None)
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
        guild_id = str(interaction.guild_id) if interaction.guild_id is not None else None
        from kingdoms.mapsdata.seed import fetch_map_seed

        seed = await fetch_map_seed(name)
        if seed is None:
            await interaction.response.send_message(
                f'Aucune page Liquipedia pour "{name}" \u2014 cr\u00e9e la map localement.',
                ephemeral=True,
            )
            return
        try:
            entry = await service.create_map(
                self.game_key,
                seed.name,
                filename=seed.name.replace(" ", "_").lower(),
                description=seed.description,
                resource_url=seed.resource_url,
                owner_guild_id=guild_id,
                map_type=seed.map_type,
            )
        except Exception:
            logger.exception("GAMES ADMIN: map import failed")
            await interaction.response.send_message(
                await reply(interaction, "replies_shared.import_failed"), ephemeral=True
            )
            return
        if seed.image_url:
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
        await interaction.response.send_message(
            f"Map **{entry.name}** importee \u2014 son post apparaitra au prochain passage du forum.",
            ephemeral=True,
        )


class GamesPoolImportButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:pools:import:(?P<game_key>[a-z0-9_]+)",
):
    """Import one published pool by its unique id (from another guild)."""

    def __init__(self, game_key: str) -> None:
        self.game_key = game_key
        super().__init__(
            discord.ui.Button(
                label="Import a map pool",
                emoji="\U0001f4e5",
                style=discord.ButtonStyle.secondary,
                custom_id=f"{_NS}:pools:import:{game_key}"[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> GamesPoolImportButton:
        """Rebuild the item from the wire (game key from the custom_id)."""
        del interaction, item
        return cls(match.group("game_key"))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Guard, then open the pool import modal."""
        from kingdoms.discord.maps_pool_flow import _guard_admin

        if not await _guard_admin(interaction):
            return
        await interaction.response.send_modal(GamesPoolImportModal(self.game_key))


class GamesPoolImportModal(discord.ui.Modal):
    """The pool import form: the source pool's unique id."""

    def __init__(self, game_key: str) -> None:
        self.game_key = game_key
        super().__init__(title="Import a map pool", timeout=None)
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
            await interaction.response.send_message("Ce pool appartient deja a cette guilde.", ephemeral=True)
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
        GamesMapCreateButton,
        GamesPoolCreateButton,
        GamesPoolEditSelect,
        GamesPoolRenameButton,
        GamesPoolArchiveButton,
        GamesPoolMapToggle,
        GamesPoolDuplicateButton,
        GamesPoolSendButton,
        GamesMapImportButton,
        GamesPoolImportButton,
        GamesGrantedSelect,
        GamesCivsReloadButton,
    )
