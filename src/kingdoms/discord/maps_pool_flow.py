"""The maps→pools flow: forum-driven pool composition (#04fcb94c).

Pool composition lives on the map surfaces, not in an admin select hit by
Discord's 25-option cap: each map's forum post (``games/<game>-maps``)
carries an **Add to pool** button (admin-guarded at click time), answered
by an ephemeral pool picker — the guild's own pools plus the public ones,
and a create-new branch. Each pool gets its own read-only forum under
``Ladder/map-pools`` (one post per member map, with a **Remove** button),
so the pool's content is visible and managed where players read it.

Namespaces (the §3b uniqueness rule): ``games:map:add:<map_id>`` for the
map-side entry point, ``games:pool:pick:<map_id>`` for the picker, and
``games:pool:remove:<map_id>:<pool_id>`` for the pool-side remove — all
DynamicItems, restart-proof, guild-scoped at click time.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import discord

from kingdoms.core.services.game_data import MAX_POOL_MAPS
from kingdoms.discord.commands_i18n import reply

logger = logging.getLogger("kingdoms.games.pool_flow")

_ADD_NS = "games:map:add"
_PICK_NS = "games:pool:pick"
_REMOVE_NS = "games:pool:remove"


def _games_wiring() -> Any | None:
    """Build the game-data wiring; None when Mongo is not configured."""
    from kingdoms.discord.wiring import build_games_service

    return build_games_service()


async def _guard_admin(interaction: discord.Interaction) -> bool:
    """Deny non-bot-admins ephemerally; True when the clicker may proceed.

    The maps and map pools are platform-wide catalog data — every
    mutation surface (posts' action bars, pool posts, the import and
    create flows) is reserved to the BOT_ADMINS operators, not the
    guild administrators.
    """
    from kingdoms.discord.wiring import guard_bot_admin

    return await guard_bot_admin(interaction, denied_message="Réservé aux bot admins.")


class MapAddToPoolButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_ADD_NS}:(?P<map_id>.+)",
):
    """The map post's entry point: add this map to a pool (admins)."""

    def __init__(self, map_id: str, label: str = "Add to map pool") -> None:
        self.map_id = map_id
        super().__init__(
            discord.ui.Button(
                label=label,
                emoji="\u2795",
                custom_id=f"{_ADD_NS}:{map_id}"[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> MapAddToPoolButton:
        """Rebuild from the wire; the map id rides the custom_id."""
        from kingdoms.discord.commands_i18n import tr

        return cls(match.group("map_id"), await tr(interaction, "ui.games.add_to_pool", "Add to map pool"))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Answer with the ephemeral pool picker (admin-gated)."""
        if not await _guard_admin(interaction):
            return
        service = _games_wiring()
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        options: list[discord.SelectOption] = []
        if service is not None and guild_id:
            entry = await service.get_map(self.map_id)
            game_key = entry.game_key if entry else "aoe2"
            pools = await service.list_map_pools(game_key, guild_id=guild_id)
            editable = [
                p for p in pools if p.edition_mode and (p.owner_guild_id == guild_id or p.owner_guild_id is None)
            ]
            options = [discord.SelectOption(label=p.name, value=p.id) for p in editable[:24]]
        from kingdoms.discord.commands_i18n import tr

        options.append(
            discord.SelectOption(
                label=await tr(interaction, "ui.games.new_pool_option", "+ New pool..."),
                value="__new__",
            )
        )
        view = discord.ui.LayoutView(timeout=None)
        view.add_item(discord.ui.Container(discord.ui.TextDisplay("## Ajouter à quel map pool ?")))
        row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
        row.add_item(MapPoolPickerSelect(self.map_id, options))
        view.add_item(row)
        await interaction.response.send_message(view=view, ephemeral=True)


class MapPoolPickerSelect(
    discord.ui.DynamicItem[discord.ui.Select[Any]],
    template=rf"{_PICK_NS}:(?P<map_id>.+)",
):
    """The ephemeral pool picker: one pool, or the create-new branch."""

    def __init__(self, map_id: str, options: list[discord.SelectOption]) -> None:
        self.map_id = map_id
        super().__init__(
            discord.ui.Select(
                custom_id=f"{_PICK_NS}:{map_id}"[:100],
                options=options or [discord.SelectOption(label="No map pool", value="none")],
                placeholder="Pick a map pool...",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> MapPoolPickerSelect:
        """Rebuild the options at click time (guild-scoped pools)."""
        service = _games_wiring()
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        map_id = match.group("map_id")
        options: list[discord.SelectOption] = []
        if service is not None and guild_id:
            entry = await service.get_map(map_id)
            if entry is not None:
                pools = await service.list_map_pools(entry.game_key, guild_id=guild_id)
                editable = [
                    p for p in pools if p.edition_mode and (p.owner_guild_id == guild_id or p.owner_guild_id is None)
                ]
                options = [discord.SelectOption(label=p.name, value=p.id) for p in editable[:24]]
        from kingdoms.discord.commands_i18n import tr

        options.append(
            discord.SelectOption(
                label=await tr(interaction, "ui.games.new_pool_option", "+ New pool..."),
                value="__new__",
            )
        )
        return cls(map_id, options)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Add the map to the chosen pool (or open the create branch)."""
        service = _games_wiring()
        if service is None:
            await interaction.response.send_message(
                await reply(interaction, "replies_shared.wiring_unavailable"), ephemeral=True
            )
            return
        chosen = (self.item.values or [""])[0]
        entry = await service.get_map(self.map_id)
        if entry is None:
            await interaction.response.send_message(
                await reply(interaction, "replies_shared.map_not_found"), ephemeral=True
            )
            return
        if chosen == "__new__":
            await interaction.response.send_modal(MapNewPoolModal(self.map_id, entry.game_key))
            return
        pool = await service.get_map_pool(chosen)
        if pool is None:
            await interaction.response.send_message(
                await reply(interaction, "replies_shared.pool_not_found"), ephemeral=True
            )
            return
        if self.map_id in pool.map_ids:
            await interaction.response.send_message(
                f"**{entry.name}** est d\u00e9j\u00e0 dans **{pool.name}**.",
                ephemeral=True,
            )
            return
        try:
            await service.update_map_pool(pool.id, map_ids=(*pool.map_ids, self.map_id))
        except Exception:
            logger.warning("POOL FLOW: add to existing pool failed", exc_info=True)
            await interaction.response.send_message(
                await reply(interaction, "replies_shared.action_failed"), ephemeral=True
            )
            return
        await interaction.response.send_message(f"**{entry.name}** ajoutée à **{pool.name}**.", ephemeral=True)
        try:
            from kingdoms.discord.pools_forum import sync_pools_forum

            if interaction.guild_id is not None:
                await sync_pools_forum(str(interaction.guild_id), interaction.client)
        except Exception:
            logger.warning("POOL FLOW: pools forum sync failed — best-effort")


class MapNewPoolModal(discord.ui.Modal):
    """The create-pool-and-add form: name + public toggle."""

    def __init__(self, map_id: str, game_key: str) -> None:
        self.map_id = map_id
        self.game_key = game_key
        super().__init__(title="Nouveau map pool", timeout=None)
        self.name: discord.ui.TextInput[Any] = discord.ui.TextInput(label="Nom du pool", max_length=64, required=True)
        self.add_item(self.name)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Create the pool (owned by this guild) and add the map."""
        service = _games_wiring()
        if service is None:
            await interaction.response.send_message(
                await reply(interaction, "replies_shared.wiring_unavailable"), ephemeral=True
            )
            return
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        try:
            pool = await service.create_map_pool(
                self.game_key,
                str(self.name.value).strip(),
                map_ids=(self.map_id,),
                owner_guild_id=guild_id or None,
                is_public=False,
            )
        except Exception:
            logger.warning("POOL FLOW: pool create failed", exc_info=True)
            await interaction.response.send_message(
                await reply(interaction, "replies_shared.creation_failed"), ephemeral=True
            )
            return
        await interaction.response.send_message(f"Pool **{pool.name}** créé — la map a été ajoutée.", ephemeral=True)
        try:
            from kingdoms.discord.pools_forum import sync_pools_forum

            if interaction.guild_id is not None:
                await sync_pools_forum(str(interaction.guild_id), interaction.client)
        except Exception:
            logger.warning("POOL FLOW: pools forum sync after create failed — best-effort")


_ADD_TO_POOL_NS = "games:pool:addmap"


class PoolAddMapButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_ADD_TO_POOL_NS}:(?P<pool_id>.+)",
):
    """The pool post's add action: open the map picker for this pool (admins)."""

    def __init__(self, pool_id: str, label: str = "Ajouter une map") -> None:
        self.pool_id = pool_id
        super().__init__(
            discord.ui.Button(
                label=label,
                emoji="\u2795",
                style=discord.ButtonStyle.success,
                custom_id=f"{_ADD_TO_POOL_NS}:{pool_id}"[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> PoolAddMapButton:
        """Rebuild from the wire; the pool id rides the custom_id."""
        return cls(match.group("pool_id"))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Answer with the ephemeral paginated map picker (admin-gated)."""
        if not await _guard_admin(interaction):
            return
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
        view = await _build_compose_view(service, pool, interaction)
        await view.build(interaction)


_REMOVE_FROM_POOL_NS = "games:pool:removemaps"


class PoolRemoveMapsButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_REMOVE_FROM_POOL_NS}:(?P<pool_id>.+)",
):
    """The pool post's remove action: multi-uncheck the pool's maps (admins)."""

    def __init__(self, pool_id: str, label: str = "Retirer des maps") -> None:
        self.pool_id = pool_id
        super().__init__(
            discord.ui.Button(
                label=label,
                emoji="\U0001F5D1",
                style=discord.ButtonStyle.danger,
                custom_id=f"{_REMOVE_FROM_POOL_NS}:{pool_id}"[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> PoolRemoveMapsButton:
        """Rebuild from the wire; the pool id rides the custom_id."""
        return cls(match.group("pool_id"))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Answer with the ephemeral multi-select of the pool's maps."""
        if not await _guard_admin(interaction):
            return
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
        entries = []
        for map_id in pool.map_ids:
            entry = await service.get_map(map_id)
            if entry is not None:
                entries.append(entry)
        if not entries:
            await interaction.response.send_message("Le pool est vide.", ephemeral=True)
            return
        view = await _build_compose_view(service, pool, interaction)
        await view.build(interaction)


async def _build_compose_view(
    service: Any, pool: Any, interaction: discord.Interaction
) -> PoolComposeView:
    """Resolve the pool's members and the guild's available maps.

    Available: the maps the guild may add \u2014 its own locals plus the
    public catalog, never the pool's current members. Members: the
    pool's maps (at most MAX_POOL_MAPS, always select-fit).
    """
    guild_id = str(interaction.guild_id) if interaction.guild_id else ""
    members = []
    for map_id in pool.map_ids:
        entry = await service.get_map(map_id)
        if entry is not None:
            members.append(entry)
    available = [
        m
        for m in await service.list_maps(pool.game_key)
        if m.archived_at is None
        and m.id not in pool.map_ids
        and (m.owner_guild_id is None or m.owner_guild_id == guild_id)
    ]
    return PoolComposeView(pool, available, members)


class PoolComposeView(discord.ui.View):
    """The pool's composition editor: add and remove in one ephemeral view.

    Two selects side by side: the guild's available maps (not in the
    pool, 25-option cap) and the pool's own maps (at most 20 — the
    pool cap). When the guild's catalog exceeds the select's cap the
    add path becomes a parsed text block (the bulk-import contract:
    one map per line, id or name). One apply button; the guard rails
    (only available maps in, only members out, the 20-map cap) run at
    apply time.
    """

    def __init__(self, pool: Any, available: list[Any], members: list[Any]) -> None:
        super().__init__(timeout=300)
        self.pool = pool
        self.available = available
        self.members = members

    async def build(self, interaction: discord.Interaction) -> None:
        """Answer with the two selects (or the parsed block over 25)."""
        if self.available and len(self.available) <= 25:
            add_select: discord.ui.Select[Any] = discord.ui.Select(
                placeholder="Maps \u00e0 ajouter (pas encore dans le pool)",
                options=[discord.SelectOption(label=m.name, value=m.id) for m in self.available[:25]],
                min_values=1,
                max_values=min(len(self.available), 25),
            )
            add_select.callback = self._on_apply  # type: ignore[method-assign]
            self.add_item(add_select)
        elif self.available:
            block_btn: discord.ui.Button[Any] = discord.ui.Button(
                label="Ajouter par saisie (id ou nom)",
                emoji="\U0001F4DD",
                style=discord.ButtonStyle.secondary,
            )
            block_btn.callback = self._on_block  # type: ignore[method-assign]
            self.add_item(block_btn)
        if self.members:
            remove_select: discord.ui.Select[Any] = discord.ui.Select(
                placeholder="Maps \u00e0 retirer (d\u00e9j\u00e0 dans le pool)",
                options=[discord.SelectOption(label=m.name, value=m.id) for m in self.members[:25]],
                min_values=1,
                max_values=len(self.members[:25]),
            )
            remove_select.callback = self._on_apply  # type: ignore[method-assign]
            self.add_item(remove_select)
        apply_btn: discord.ui.Button[Any] = discord.ui.Button(
            label="Appliquer", style=discord.ButtonStyle.primary
        )
        apply_btn.callback = self._on_apply  # type: ignore[method-assign]
        self.add_item(apply_btn)
        note = f"Pool **{self.pool.name}** \u2014 {len(self.pool.map_ids)}/{MAX_POOL_MAPS} maps"
        if self.available and len(self.available) > 25:
            note += (
                "\nLe catalogue d\u00e9passe 25 maps \u2014 saisis les maps \u00e0 ajouter dans le bloc "
                "de texte (une par ligne : id ou nom, comme l'import Liquipedia)."
            )
        await interaction.response.send_message(note, view=self, ephemeral=True)

    async def _on_block(self, interaction: discord.Interaction) -> None:
        """Open the parsed-block modal (the over-25 add path)."""
        await interaction.response.send_modal(PoolAddBlockModal(self.pool))

    async def _on_apply(self, interaction: discord.Interaction) -> None:
        """Collect both selections, delegate to the apply step."""
        add_ids, remove_ids = self._collect()
        if not add_ids and not remove_ids:
            await interaction.response.edit_message(
                content=await reply(interaction, "replies_shared.selection_invalid")
            )
            return
        await _apply_composition(interaction, self.pool, add_ids, remove_ids)

    def _collect(self) -> tuple[list[str], list[str]]:
        """Gather the add selection (select or parsed block) and the remove one."""
        add_ids: list[str] = []
        remove_ids: list[str] = []
        for child in self.children:
            if isinstance(child, discord.ui.Select):
                if "ajouter" in (child.placeholder or ""):
                    add_ids.extend(child.values or [])
                else:
                    remove_ids.extend(child.values or [])
        return add_ids, remove_ids


async def _apply_composition(
    interaction: discord.Interaction, pool_ref: Any, add_ids: list[str], remove_ids: list[str]
) -> None:
    """Validate the rails, write the composition, sync the post, report."""
    service = _games_wiring()
    if service is None:
        await interaction.response.edit_message(
            content=await reply(interaction, "replies_shared.wiring_unavailable")
        )
        return
    pool = await service.get_map_pool(pool_ref.id)
    if pool is None:
        await interaction.response.edit_message(
            content=await reply(interaction, "replies_shared.pool_not_found")
        )
        return
    catalog = {m.id: m for m in await service.list_maps(pool.game_key)}
    chosen_add, skipped = _resolve_additions(catalog, pool, add_ids)
    chosen_remove = [mid for mid in remove_ids if mid in pool.map_ids]
    remaining = tuple(mid for mid in pool.map_ids if mid not in chosen_remove)
    final = remaining + tuple(mid for mid in chosen_add if mid not in remaining)
    if len(final) > MAX_POOL_MAPS:
        await interaction.response.edit_message(
            content=(
                f"Un map pool tient au plus {MAX_POOL_MAPS} maps \u2014 "
                f"la s\u00e9lection en donne {len(final)}. Retire des maps d'abord."
            )
        )
        return
    try:
        updated = await service.update_map_pool(pool.id, map_ids=tuple(final))
    except Exception:
        logger.warning("POOL FLOW: compose apply failed", exc_info=True)
        await interaction.response.edit_message(
            content=await reply(interaction, "replies_shared.action_failed")
        )
        return
    await interaction.response.edit_message(
        content=_compose_report(pool, updated, catalog, chosen_add, chosen_remove, skipped)
    )
    try:
        from kingdoms.discord.pools_forum import sync_pools_forum

        if interaction.guild_id is not None:
            await sync_pools_forum(str(interaction.guild_id), interaction.client)
    except Exception:
        logger.warning("POOL FLOW: pools forum sync after compose failed \u2014 best-effort")


class PoolAddBlockModal(discord.ui.Modal):
    """The over-25 add path: a parsed block, one map per line (id or name)."""

    def __init__(self, pool: Any) -> None:
        self.pool = pool
        super().__init__(title=f"Ajouter \u00e0 {pool.name} (liste)", timeout=300)
        self.lines: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label="Maps (id ou nom, une par ligne)",
            style=discord.TextStyle.paragraph,
            max_length=3800,
            required=True,
        )
        self.add_item(self.lines)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Guard, parse the block, apply through the shared composition step."""
        if not await _guard_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        from kingdoms.mapsdata.bulk_import import parse_import_line

        add_ids: list[str] = []
        for line in str(self.lines.value or "").splitlines():
            parsed = parse_import_line(line.strip())
            if parsed.raw and not parsed.error:
                add_ids.append(parsed.raw)
        if not add_ids:
            await interaction.followup.send(
                await reply(interaction, "replies_shared.selection_invalid"), ephemeral=True
            )
            return
        await _apply_composition(interaction, self.pool, add_ids, [])



def _resolve_additions(
    catalog: dict[str, Any], pool: Any, add_ids: list[str]
) -> tuple[list[str], list[str]]:
    """Filter the raw add inputs to real, addable maps (id or name)."""
    chosen: list[str] = []
    skipped: list[str] = []
    for raw in add_ids:
        entry = catalog.get(raw) or next(
            (m for m in catalog.values() if m.name.lower() == raw.lower()), None
        )
        if entry is None or entry.archived_at is not None:
            skipped.append(raw)
            continue
        if entry.id in pool.map_ids:
            continue
        chosen.append(entry.id)
    return chosen, skipped


def _compose_report(
    pool: Any,
    updated: Any,
    catalog: dict[str, Any],
    chosen_add: list[str],
    chosen_remove: list[str],
    skipped: list[str],
) -> str:
    """Render the apply outcome: the new count, the adds, the removes."""
    parts = [f"Pool **{pool.name}** mis \u00e0 jour \u2014 {len(updated.map_ids)} maps."]
    if chosen_add:
        parts.append(f"Ajout\u00e9es : {', '.join(catalog[m].name for m in chosen_add if m in catalog)}.")
    if chosen_remove:
        parts.append(
            f"Retir\u00e9es : {', '.join(catalog[m].name for m in chosen_remove if m in catalog)}."
        )
    if skipped:
        parts.append(f"Introuvables (ignor\u00e9es) : {', '.join(skipped[:10])}.")
    return "\n".join(parts)



class PoolAddMapByIdModal(discord.ui.Modal):
    """Add one map to a pool by id (map:public:arena) — the over-25 path."""

    def __init__(self, pool_id: str, pool_name: str = "") -> None:
        self.pool_id = pool_id
        super().__init__(title=f"Ajouter à {pool_name or 'pool'} (id)", timeout=None)
        self.map_id: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label="Map id (ex. map:public:arena)",
            max_length=100,
            required=True,
        )
        self.add_item(self.map_id)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Guard, resolve the id, add the map, sync the pool post."""
        from kingdoms.discord.maps_pool_flow import _guard_admin

        if not await _guard_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        service = _games_wiring()
        if service is None:
            await interaction.followup.send("Wiring indisponible.", ephemeral=True)
            return
        map_id = str(self.map_id.value or "").strip()
        pool = await service.get_map_pool(self.pool_id)
        entry = await service.get_map(map_id)
        if pool is None or entry is None:
            await interaction.followup.send("Map ou pool introuvable.", ephemeral=True)
            return
        if entry.id in pool.map_ids:
            await interaction.followup.send(
                f"**{entry.name}** est d\u00e9j\u00e0 dans **{pool.name}**.", ephemeral=True
            )
            return
        try:
            await service.update_map_pool(pool.id, map_ids=(*pool.map_ids, entry.id))
        except Exception as exc:
            logger.warning("POOL FLOW: add by id failed", exc_info=True)
            await interaction.followup.send(f"Échec : {exc}", ephemeral=True)
            return
        try:
            from kingdoms.discord.pools_forum import sync_pools_forum

            if interaction.guild_id is not None:
                await sync_pools_forum(str(interaction.guild_id), interaction.client)
        except Exception:
            logger.warning("POOL FLOW: pools forum sync failed — best-effort")
        await interaction.followup.send(
            f"**{entry.name}** ajoutée à **{pool.name}**.", ephemeral=True
        )


class PoolDeleteButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"games:pool:delete:(?P<pool_id>.+)",
):
    """A pool post's delete action: archive (reversible) + forum sync."""

    def __init__(self, pool_id: str, game_key: str = "") -> None:
        self.pool_id = pool_id
        self.game_key = game_key
        super().__init__(
            discord.ui.Button(
                label="Supprimer",
                emoji="\U0001F5D1",
                style=discord.ButtonStyle.danger,
                custom_id=(f"games:pool:delete:{pool_id}")[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> PoolDeleteButton:
        """Rebuild from the wire (pool id from the custom_id)."""
        del interaction, item
        return cls(match.group("pool_id"))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Guard, then open the type-the-name confirmation modal."""
        if not await _guard_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        service = _games_wiring()
        if service is None:
            await interaction.followup.send("Wiring indisponible.", ephemeral=True)
            return
        pool = await service.get_map_pool(self.pool_id)
        if pool is None:
            await interaction.followup.send("Pool introuvable.", ephemeral=True)
            return
        await interaction.followup.send(
            view=_pool_delete_confirm_view(self.pool_id, pool.name),
            ephemeral=True,
        )


def _pool_delete_confirm_view(pool_id: str, name: str) -> discord.ui.View:
    """Build the confirmation view: one button opening the type-the-name modal."""
    import discord as _discord

    view = _discord.ui.View(timeout=180)

    async def _open(interaction: _discord.Interaction) -> None:
        await interaction.response.send_modal(PoolDeleteConfirmModal(pool_id, name))

    class _ConfirmButton(_discord.ui.Button[Any]):
        def __init__(self) -> None:
            super().__init__(
                label="Confirmer la suppression",
                style=_discord.ButtonStyle.danger,
                emoji="\U0001F5D1",
            )

        async def callback(self, interaction: _discord.Interaction) -> None:
            """Open the deletion modal."""
            await _open(interaction)

    view.add_item(_ConfirmButton())
    return view


class MapDeleteConfirmModal(discord.ui.Modal):
    """Type-the-name confirmation for deleting a map.

    The delete is irreversible for a guild map and reversible
    (archive) for a public one; both require the admin to retype the
    map's exact name — a mistyped or empty answer refuses.
    """

    def __init__(self, map_id: str, game_key: str, expected_name: str) -> None:
        self.map_id = map_id
        self.game_key = game_key
        self.expected_name = expected_name
        super().__init__(title=f"Supprimer {expected_name}", timeout=None)
        self.confirm: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label=f"Tape le nom exact ({expected_name}) pour confirmer",
            max_length=64,
            required=True,
        )
        self.add_item(self.confirm)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Refuse unless the typed name matches; delete + delete the post."""
        if not await _guard_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        typed = str(self.confirm.value or "").strip()
        if typed.casefold() != self.expected_name.casefold():
            await interaction.followup.send(
                f"Nom ne correspond pas — abandon. Attendu : **{self.expected_name}**.",
                ephemeral=True,
            )
            return
        service = _games_wiring()
        if service is None:
            await interaction.followup.send("Wiring indisponible.", ephemeral=True)
            return
        entry = await service.get_map(self.map_id)
        if entry is None:
            await interaction.followup.send("Map introuvable.", ephemeral=True)
            return
        try:
            if entry.owner_guild_id:
                await service.delete_map(self.map_id)
                outcome = f"Map **{entry.name}** supprimée définitivement."
            else:
                await service.archive_map(self.map_id)
                outcome = f"Map **{entry.name}** désactivée (archivée) — réactivable."
        except ValueError as exc:
            await interaction.followup.send(
                f"Impossible de supprimer **{entry.name}** — elle fait partie d'un map pool. "
                f"Retire-la du pool d'abord ({exc}).",
                ephemeral=True,
            )
            return
        except Exception as exc:
            logger.exception("MAPS: confirmed delete failed (%s)", self.map_id)
            await interaction.followup.send(f"Échec : {exc}", ephemeral=True)
            return
        await _delete_map_post(interaction, entry)
        await interaction.followup.send(outcome + " Le post est supprimé.", ephemeral=True)


async def _delete_map_post(interaction: discord.Interaction, entry: Any) -> None:
    """Delete the map's forum post (best-effort) after a delete."""
    if entry.forum_message_id and interaction.guild_id is not None:
        from kingdoms.discord.admin_panel_dynamic import _panel_client_ref

        client = _panel_client_ref()
        guild = client.get_guild(interaction.guild_id) if client else None
        if guild is not None:
            try:
                thread = await guild.fetch_channel(int(entry.forum_message_id))
                if thread is not None:
                    await thread.delete(reason=f"kingdoms: map {entry.name} deleted")
            except Exception:
                logger.warning(
                    "map post delete failed (thread %s) — best-effort",
                    entry.forum_message_id,
                    exc_info=True,
                )
        try:
            from kingdoms.discord.pinned_views import refresh_registered_pins

            await refresh_registered_pins(str(interaction.guild_id))
        except Exception:
            logger.warning("pin refresh after map delete failed — best-effort")


class PoolDeleteConfirmModal(discord.ui.Modal):
    """Type-the-name confirmation for deleting a map pool."""

    def __init__(self, pool_id: str, expected_name: str) -> None:
        self.pool_id = pool_id
        self.expected_name = expected_name
        super().__init__(title=f"Supprimer {expected_name}", timeout=None)
        self.confirm: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label=f"Tape le nom exact ({expected_name}) pour confirmer",
            max_length=64,
            required=True,
        )
        self.add_item(self.confirm)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Refuse unless the typed name matches; archive + delete the post."""
        if not await _guard_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        typed = str(self.confirm.value or "").strip()
        if typed.casefold() != self.expected_name.casefold():
            await interaction.followup.send(
                f"Nom ne correspond pas — abandon. Attendu : **{self.expected_name}**.",
                ephemeral=True,
            )
            return
        service = _games_wiring()
        if service is None:
            await interaction.followup.send("Wiring indisponible.", ephemeral=True)
            return
        pool = await service.get_map_pool(self.pool_id)
        if pool is None:
            await interaction.followup.send("Pool introuvable.", ephemeral=True)
            return
        try:
            await service.archive_map_pool(self.pool_id)
        except Exception as exc:
            logger.warning("POOL FLOW: confirmed pool delete failed", exc_info=True)
            await interaction.followup.send(f"Suppression impossible — {exc}", ephemeral=True)
            return
        await interaction.followup.send(
            f"Pool **{pool.name}** supprimé — son post disparaît au prochain sync.",
            ephemeral=True,
        )
        try:
            from kingdoms.discord.pools_forum import sync_pools_forum

            if interaction.guild_id is not None:
                await sync_pools_forum(str(interaction.guild_id), interaction.client)
        except Exception:
            logger.warning("pools sync after confirmed delete failed — best-effort")



class MapDeleteButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"games:map:delete:(?P<map_id>.+):(?P<game_key>[a-z0-9_]+):(?P<owned>[01])",
):
    """A map post's delete action.

    A guild-owned map is deleted for good (hard delete); a public map
    is archived (reversible) — the catalog seed owns it, another guild
    may still use it.
    """

    def __init__(self, map_id: str, game_key: str = "", owned_by_guild: bool = False) -> None:
        self.map_id = map_id
        self.game_key = game_key
        self.owned_by_guild = owned_by_guild
        label = "Supprimer" if owned_by_guild else "Désactiver"
        super().__init__(
            discord.ui.Button(
                label=label,
                emoji="\U0001F5D1",
                style=discord.ButtonStyle.danger,
                custom_id=(f"games:map:delete:{map_id}:{game_key}:{1 if owned_by_guild else 0}")[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> MapDeleteButton:
        """Rebuild from the wire (map id, game, ownership flag)."""
        del interaction, item
        return cls(match.group("map_id"), match.group("game_key"), match.group("owned") == "1")

    async def callback(self, interaction: discord.Interaction) -> None:
        """Guard, then open the type-the-name confirmation modal."""
        from kingdoms.discord.maps_pool_flow import _guard_admin

        if not await _guard_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        service = _games_wiring()
        if service is None:
            await interaction.followup.send("Wiring indisponible.", ephemeral=True)
            return
        entry = await service.get_map(self.map_id)
        if entry is None:
            await interaction.followup.send("Map introuvable.", ephemeral=True)
            return
        await interaction.followup.send(
            view=_map_delete_confirm_view(self.map_id, self.game_key, entry.name),
            ephemeral=True,
        )


def _map_delete_confirm_view(map_id: str, game_key: str, name: str) -> discord.ui.View:
    """Build the confirmation view: one button opening the type-the-name modal."""
    import discord as _discord

    view = _discord.ui.View(timeout=180)

    async def _open(interaction: _discord.Interaction) -> None:
        await interaction.response.send_modal(
            MapDeleteConfirmModal(map_id, game_key, name)
        )

    class _ConfirmButton(_discord.ui.Button[Any]):
        def __init__(self) -> None:
            super().__init__(
                label="Confirmer la suppression",
                style=_discord.ButtonStyle.danger,
                emoji="\U0001F5D1",
            )

        async def callback(self, interaction: _discord.Interaction) -> None:
            """Open the deletion modal."""
            await _open(interaction)

    view.add_item(_ConfirmButton())
    return view


class MapEditButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"games:map:edit:(?P<map_id>.+)",
):
    """A guild-owned map post's edit action: open the edit modal (admins)."""

    def __init__(self, map_id: str) -> None:
        self.map_id = map_id
        super().__init__(
            discord.ui.Button(
                label="Edit the map",
                emoji="\u270f\ufe0f",
                style=discord.ButtonStyle.secondary,
                custom_id=("games:map:edit:" + map_id)[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> MapEditButton:
        """Rebuild the item from the wire (map id from the custom_id)."""
        del interaction, item
        return cls(match.group("map_id"))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Guard, then open the edit modal for this guild map."""
        if not await _guard_admin(interaction):
            return
        service = _games_wiring()
        if service is None:
            await interaction.response.send_message(
                await reply(interaction, "replies_shared.wiring_unavailable"), ephemeral=True
            )
            return
        entry = await service.get_map(self.map_id)
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        if entry is None:
            await interaction.response.send_message(
                await reply(interaction, "replies_shared.map_not_found"), ephemeral=True
            )
            return
        if entry.owner_guild_id != guild_id:
            await interaction.response.send_message(
                await reply(interaction, "replies_shared.guild_maps_only"), ephemeral=True
            )
            return
        await interaction.response.send_modal(MapEditModal(self.map_id, entry.name))


class MapEditModal(discord.ui.Modal):
    """The guild map's edit form: name, description, resource URL."""

    def __init__(self, map_id: str, current_name: str) -> None:
        self.map_id = map_id
        super().__init__(title="Edit the map", timeout=None)
        self.name: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label="Nom", max_length=64, required=True, default=current_name
        )
        self.description: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label="Description", max_length=256, required=False, style=discord.TextStyle.paragraph
        )
        self.resource_url: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label="URL de la fiche (liquipedia)", max_length=200, required=False
        )
        self.add_item(self.name)
        self.add_item(self.description)
        self.add_item(self.resource_url)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Persist the edits; the post refreshes at the next sync."""
        service = _games_wiring()
        if service is None:
            await interaction.response.send_message(
                await reply(interaction, "replies_shared.wiring_unavailable"), ephemeral=True
            )
            return
        try:
            entry = await service.update_map(
                self.map_id,
                name=str(self.name.value or "").strip() or None,
                description=str(self.description.value or "").strip() or None,
                resource_url=str(self.resource_url.value or "").strip() or None,
            )
        except Exception:
            logger.warning("POOL FLOW: map edit failed", exc_info=True)
            await interaction.response.send_message(
                await reply(interaction, "replies_shared.edit_failed"), ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"Map **{entry.name}** mise à jour — la fiche se rafraîchit à la prochaine sync.",
            ephemeral=True,
        )


_TRANSITION_NS = "games:pool:state"


def register_pool_flow_items(bot: discord.Client) -> None:
    """Register the flow's DynamicItems (called at every startup)."""
    bot.add_dynamic_items(MapAddToPoolButton)
    bot.add_dynamic_items(MapPoolPickerSelect)
    bot.add_dynamic_items(PoolAddMapButton)
    bot.add_dynamic_items(MapEditButton)
    bot.add_dynamic_items(MapDeleteButton)
    bot.add_dynamic_items(PoolDeleteButton)
    bot.add_dynamic_items(PoolRemoveMapsButton)
