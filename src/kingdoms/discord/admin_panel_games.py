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
import os
import re
from typing import Any

import discord

from kingdoms.discord.admin_panel_mods import AdminModSection, register_admin_mod_section

logger = logging.getLogger("kingdoms.games.admin_panel")

MOD_KEY = "games"
_NS = "admin:pin:modgames"
GAME_KEY = "aoe2"  # default context; the picker may switch it per view


def _back_row() -> discord.ui.ActionRow[discord.ui.LayoutView]:
    """Build the back row shared by every sub-view (returns to the games entry)."""
    row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    row.add_item(GamesBackButton())
    return row


def _games_wiring() -> Any | None:
    """Build the game-data wiring; None when Mongo is not configured."""
    if not os.environ.get("MONGO_URI"):
        return None
    try:
        from kingdoms.core.games.aoe2.seed import MongoAoE2Database
        from kingdoms.core.models.db import get_async_database
        from kingdoms.core.services.game_data import GameDataService

        return GameDataService(MongoAoE2Database(get_async_database()))
    except Exception:
        logger.exception("GAMES ADMIN: wiring failed")
        return None


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
        blocks.append(discord.ui.TextDisplay("Wiring indisponible : Mongo n'est pas configure."))
        view.add_item(discord.ui.Container(*blocks, accent_colour=discord.Colour(0x5865F2)))
        view.add_item(_back_row())
        return view
    keys = await service.list_game_keys()
    if not keys:
        blocks.append(discord.ui.TextDisplay("_Aucun jeu connu - seed un catalogue._"))
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
                options=options or [discord.SelectOption(label="Aucun jeu", value="none")],
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
    ) -> GamesGameSelect:
        """Rebuild the select's options at click time."""
        service = _games_wiring()
        options: list[discord.SelectOption] = []
        if service is not None:
            for key in (await service.list_game_keys())[:25]:
                options.append(discord.SelectOption(label=key, value=key))
        return cls(options)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the chosen game's sub-menu."""
        chosen = (_selected_values(interaction) or [""])[0]
        if not chosen or chosen == "none":
            await interaction.response.defer()
            return
        await interaction.response.edit_message(view=await game_menu_view(chosen))


async def game_menu_view(game_key: str) -> discord.ui.LayoutView:
    """One game's sub-menu: snapshot + the maps/pools actions."""
    service = _games_wiring()
    view = discord.ui.LayoutView(timeout=None)
    blocks: list[Any] = [discord.ui.TextDisplay(f"# Jeu `{game_key}`")]
    if service is None:
        blocks.append(discord.ui.TextDisplay("Wiring indisponible."))
        view.add_item(discord.ui.Container(*blocks))
        view.add_item(_back_row())
        return view
    maps = await service.list_maps(game_key)
    pools = await service.list_map_pools(game_key)
    active_maps = [m for m in maps if m.archived_at is None]
    blocks.extend(
        [
            discord.ui.Separator(),
            discord.ui.TextDisplay(
                f"**Maps actives** : {len(active_maps)} - **Pools** : {len(pools)}"
            ),
        ]
    )
    view.add_item(discord.ui.Container(*blocks, accent_colour=discord.Colour(0x5865F2)))
    actions: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    actions.add_item(GamesMapsButton(game_key))
    actions.add_item(GamesPoolsButton(game_key))
    view.add_item(actions)
    view.add_item(_back_row())
    return view


class GamesBackButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:back",
):
    """Return to the games entry view."""

    def __init__(self) -> None:
        super().__init__(
            discord.ui.Button(
                label="Retour",
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
        """Re-render the games entry (the game picker)."""
        await interaction.response.edit_message(view=await games_admin_entry(interaction))


class GamesMapsButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:maps:(?P<game_key>[a-z0-9_]+)",
):
    """Open the game's maps sub-view."""

    def __init__(self, game_key: str) -> None:
        self.game_key = game_key
        super().__init__(
            discord.ui.Button(
                label="Maps", style=discord.ButtonStyle.primary, custom_id=f"{_NS}:maps:{game_key}"[:100]
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
        """Render the maps sub-view."""
        await interaction.response.edit_message(view=await maps_admin_view(self.game_key))


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
        await interaction.response.edit_message(view=await pools_admin_view(self.game_key))


async def maps_admin_view(game_key: str) -> discord.ui.LayoutView:
    """Render the game's maps sub-view: the catalog with archive toggles."""
    service = _games_wiring()
    view = discord.ui.LayoutView(timeout=None)
    blocks: list[Any] = [discord.ui.TextDisplay(f"# Maps ({game_key})")]
    if service is None:
        blocks.append(discord.ui.TextDisplay("Wiring indisponible."))
        view.add_item(discord.ui.Container(*blocks))
        view.add_item(_back_row())
        return view
    maps = await service.list_maps(game_key)
    if not maps:
        blocks.append(discord.ui.TextDisplay("_Aucune map - seed le catalogue._"))
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
    view.add_item(GamesMapsBackButton(game_key))
    return view


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
                placeholder="Activer / desactiver une map...",
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
            await interaction.response.send_message("Wiring indisponible.", ephemeral=True)
            return
        chosen = (_selected_values(interaction) or [""])[0]
        if not chosen or chosen == "none":
            await interaction.response.defer()
            return
        entry = await service.get_map(chosen)
        if entry is None:
            await interaction.response.send_message("Map introuvable.", ephemeral=True)
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
        await interaction.response.edit_message(view=await maps_admin_view(self.game_key))
        await interaction.followup.send(message, ephemeral=True)


async def pools_admin_view(game_key: str) -> discord.ui.LayoutView:
    """Render the game's map pools sub-view: list + CRUD actions."""
    service = _games_wiring()
    view = discord.ui.LayoutView(timeout=None)
    blocks: list[Any] = [discord.ui.TextDisplay(f"# Map pools ({game_key})")]
    if service is None:
        blocks.append(discord.ui.TextDisplay("Wiring indisponible."))
        view.add_item(discord.ui.Container(*blocks))
        view.add_item(_back_row())
        return view
    pools = await service.list_map_pools(game_key)
    if not pools:
        blocks.append(discord.ui.TextDisplay("_Aucun pool._"))
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
        super().__init__(title="Creer un map pool", timeout=None)
        self.name: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label="Nom du pool", max_length=64, required=True
        )
        self.map_name: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label="Premiere map (nom, optionnel)", max_length=64, required=False
        )
        self.add_item(self.name)
        self.add_item(self.map_name)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Create the pool (with the optional first map), confirm."""
        service = _games_wiring()
        if service is None:
            await interaction.response.send_message("Wiring indisponible.", ephemeral=True)
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
            await interaction.response.send_message("Creation echouee (voir les logs).", ephemeral=True)
            return
        await interaction.response.send_message(
            f"Pool **{pool.name}** cree - ajoute ses maps via la liste.", ephemeral=True
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
                options=options or [discord.SelectOption(label="Aucun pool", value="none")],
                placeholder="Editer un pool...",
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
        await interaction.response.edit_message(view=await pool_editor_view(chosen, self.game_key))


async def pool_editor_view(pool_id: str, game_key: str) -> discord.ui.LayoutView:
    """One pool's editor: rename, add/remove maps, archive."""
    service = _games_wiring()
    view = discord.ui.LayoutView(timeout=None)
    blocks: list[Any] = [discord.ui.TextDisplay("# Editer le pool")]
    if service is None:
        blocks.append(discord.ui.TextDisplay("Wiring indisponible."))
        view.add_item(discord.ui.Container(*blocks))
        view.add_item(_back_row())
        return view
    pool = await service.get_map_pool(pool_id)
    if pool is None:
        blocks.append(discord.ui.TextDisplay("Pool introuvable."))
        view.add_item(discord.ui.Container(*blocks))
        view.add_item(_back_row())
        return view
    maps = await service.list_maps(pool.game_key)
    names = {m.id: m.name for m in maps}
    lines = [f"**{pool.name}** - {len(pool.map_ids)} maps"]
    for map_id in pool.map_ids:
        lines.append(f"- {names.get(map_id, map_id)}")
    if not pool.map_ids:
        lines.append("_Aucune map._")
    blocks.append(discord.ui.TextDisplay("\n".join(lines)))
    view.add_item(discord.ui.Container(*blocks))
    select_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    select_row.add_item(GamesPoolMapToggle(pool_id))
    view.add_item(select_row)
    action_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    action_row.add_item(GamesPoolRenameButton(pool_id))
    action_row.add_item(GamesPoolArchiveButton(pool_id))
    view.add_item(action_row)
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
                label="Renommer", style=discord.ButtonStyle.primary, custom_id=f"{_NS}:pools:rename:{pool_id}"[:100]
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
        self.name: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label="Nouveau nom", max_length=64, required=True
        )
        self.add_item(self.name)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Rename the pool, audit, re-render the editor."""
        service = _games_wiring()
        if service is None:
            await interaction.response.send_message("Wiring indisponible.", ephemeral=True)
            return
        try:
            pool = await service.update_map_pool(self.pool_id, name=str(self.name.value).strip())
        except Exception:
            logger.exception("GAMES ADMIN: pool rename failed")
            await interaction.response.send_message("Renommage echoue (voir les logs).", ephemeral=True)
            return
        await interaction.response.edit_message(view=await pool_editor_view(pool.id, pool.game_key))
        await interaction.followup.send(f"Pool renomme **{pool.name}**.", ephemeral=True)


class GamesPoolArchiveButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:pools:archive:(?P<pool_id>[^:]+)",
):
    """Archive (delete) the pool, unless it is the active pool of a ladder."""

    def __init__(self, pool_id: str) -> None:
        self.pool_id = pool_id
        super().__init__(
            discord.ui.Button(
                label="Supprimer", style=discord.ButtonStyle.danger, custom_id=f"{_NS}:pools:archive:{pool_id}"[:100]
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
            await interaction.response.send_message("Pool introuvable.", ephemeral=True)
            return
        pool = await service.get_map_pool(self.pool_id)
        if pool is None:
            await interaction.response.send_message("Pool introuvable.", ephemeral=True)
            return
        try:
            await service.archive_map_pool(self.pool_id)
        except Exception:
            logger.exception("GAMES ADMIN: pool archive failed")
            await interaction.response.send_message(
                "Archivage echoue (pool actif d'un ladder ? voir les logs).", ephemeral=True
            )
            return
        await interaction.response.edit_message(view=await pools_admin_view(pool.game_key))
        await interaction.followup.send("Pool supprime (archive).", ephemeral=True)


class GamesPoolMapToggle(
    discord.ui.DynamicItem[discord.ui.Select[Any]],
    template=rf"{_NS}:pools:maps:(?P<pool_id>.+)",
):
    """Add or remove a map from the pool (one select, toggle semantics)."""

    def __init__(self, pool_id: str, options: list[discord.SelectOption] | None = None) -> None:
        self.pool_id = pool_id
        super().__init__(
            discord.ui.Select(
                custom_id=f"{_NS}:pools:maps:{pool_id}"[:100],
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
        """Rebuild the select's options at click time; pool id from state."""
        service = _games_wiring()
        pool_id = match.group("pool_id")
        options: list[discord.SelectOption] = []
        if service is not None and pool_id:
            pool = await service.get_map_pool(pool_id)
            maps = await service.list_maps(pool.game_key if pool else GAME_KEY)
            options = [
                discord.SelectOption(
                    label=f"{m.name}{' [dans le pool]' if pool and m.id in pool.map_ids else ''}",
                    value=m.id,
                )
                for m in maps[:25]
                if m.archived_at is None
            ]
        return cls(pool_id, options)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Toggle the chosen map in the pool, re-render the editor."""
        service = _games_wiring()
        if service is None or not self.pool_id:
            await interaction.response.send_message("Pool introuvable.", ephemeral=True)
            return
        chosen = (_selected_values(interaction) or [""])[0]
        if not chosen or chosen == "none":
            await interaction.response.defer()
            return
        pool = await service.get_map_pool(self.pool_id)
        if pool is None:
            await interaction.response.send_message("Pool introuvable.", ephemeral=True)
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
            await interaction.response.send_message("Modification echouee (voir les logs).", ephemeral=True)
            return
        await interaction.response.edit_message(view=await pool_editor_view(self.pool_id, pool.game_key))
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
        await interaction.response.edit_message(view=await game_menu_view(self.game_key))


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
        await interaction.response.edit_message(view=await game_menu_view(self.game_key))


def register_games_admin_section() -> None:
    """Register the games section into the /admin panel (idempotent)."""
    register_admin_mod_section(
        AdminModSection(
            mod=MOD_KEY,
            label="Games",
            description="Catalogues de jeux : maps, map pools",
            entry=games_admin_entry,
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
        GamesPoolCreateButton,
        GamesPoolEditSelect,
        GamesPoolRenameButton,
        GamesPoolArchiveButton,
        GamesPoolMapToggle,
    )
