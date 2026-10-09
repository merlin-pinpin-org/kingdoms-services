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

logger = logging.getLogger("kingdoms.games.pool_flow")

_ADD_NS = "games:map:add"
_PICK_NS = "games:pool:pick"
_REMOVE_NS = "games:pool:remove"


def _games_wiring() -> Any | None:
    """Build the game-data wiring; None when Mongo is not configured."""
    from kingdoms.discord.wiring import build_games_service

    return build_games_service()


async def _guard_admin(interaction: discord.Interaction) -> bool:
    """Deny non-admins ephemerally; True when the clicker may proceed."""
    from kingdoms.discord.wiring import guard_admin

    return await guard_admin(interaction)


class MapAddToPoolButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_ADD_NS}:(?P<map_id>.+)",
):
    """The map post's entry point: add this map to a pool (admins)."""

    def __init__(self, map_id: str) -> None:
        self.map_id = map_id
        super().__init__(
            discord.ui.Button(
                label="Ajouter au map pool",
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
        return cls(match.group("map_id"))

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
            options = [
                discord.SelectOption(label=f"{p.name}{' (public)' if p.is_public else ''}", value=p.id)
                for p in pools[:24]
            ]
        options.append(discord.SelectOption(label="+ Nouveau pool...", value="__new__"))
        view = discord.ui.LayoutView(timeout=None)
        view.add_item(
            discord.ui.Container(discord.ui.TextDisplay("## Ajouter à quel map pool ?"))
        )
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
                options=options or [discord.SelectOption(label="Aucun pool", value="none")],
                placeholder="Choisis un map pool...",
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
                options = [
                    discord.SelectOption(label=f"{p.name}{' (public)' if p.is_public else ''}", value=p.id)
                    for p in pools[:24]
                ]
        options.append(discord.SelectOption(label="+ Nouveau pool...", value="__new__"))
        return cls(map_id, options)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Add the map to the chosen pool (or open the create branch)."""
        service = _games_wiring()
        if service is None:
            await interaction.response.send_message("Wiring indisponible.", ephemeral=True)
            return
        chosen = (self.item.values or [""])[0]
        entry = await service.get_map(self.map_id)
        if entry is None:
            await interaction.response.send_message("Map introuvable.", ephemeral=True)
            return
        if chosen == "__new__":
            await interaction.response.send_modal(MapNewPoolModal(self.map_id, entry.game_key))
            return
        pool = await service.get_map_pool(chosen)
        if pool is None:
            await interaction.response.send_message("Pool introuvable.", ephemeral=True)
            return
        try:
            await service.update_map_pool(pool.id, map_ids=(*pool.map_ids, self.map_id))
        except Exception:
            logger.warning("POOL FLOW: add to existing pool failed", exc_info=True)
            await interaction.response.send_message("Ajout impossible (voir les logs).", ephemeral=True)
            return
        await interaction.response.send_message(
            f"**{entry.name}** ajoutée à **{pool.name}**.", ephemeral=True
        )


class MapNewPoolModal(discord.ui.Modal):
    """The create-pool-and-add form: name + public toggle."""

    def __init__(self, map_id: str, game_key: str) -> None:
        self.map_id = map_id
        self.game_key = game_key
        super().__init__(title="Nouveau map pool", timeout=None)
        self.name: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label="Nom du pool", max_length=64, required=True
        )
        self.public: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label="Public ? (oui / non)",
            placeholder="non",
            max_length=3,
            required=False,
            default="non",
        )
        self.add_item(self.name)
        self.add_item(self.public)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Create the pool (owned by this guild) and add the map."""
        service = _games_wiring()
        if service is None:
            await interaction.response.send_message("Wiring indisponible.", ephemeral=True)
            return
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        try:
            pool = await service.create_map_pool(
                self.game_key,
                str(self.name.value).strip(),
                map_ids=(self.map_id,),
                owner_guild_id=guild_id or None,
                is_public=str(self.public.value or "non").strip().lower() in {"oui", "yes", "public", "true"},
            )
        except Exception:
            logger.warning("POOL FLOW: pool create failed", exc_info=True)
            await interaction.response.send_message(
                "Création impossible (nom déjà pris ?).", ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"Pool **{pool.name}** créé — la map a été ajoutée.", ephemeral=True
        )


class PoolRemoveMapButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_REMOVE_NS}:(?P<map_id>.+):(?P<pool_id>.+)",
):
    """The pool post's remove action: drop this map from the pool (admins)."""

    def __init__(self, map_id: str, pool_id: str, label: str | None = None) -> None:
        self.map_id = map_id
        self.pool_id = pool_id
        super().__init__(
            discord.ui.Button(
                label=label or "Retirer du pool",
                emoji="🗑️",
                style=discord.ButtonStyle.danger,
                custom_id=f"{_REMOVE_NS}:{map_id}:{pool_id}"[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> PoolRemoveMapButton:
        """Rebuild from the wire; both ids ride the custom_id."""
        return cls(match.group("map_id"), match.group("pool_id"))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Remove the map from the pool, confirm ephemerally."""
        if not await _guard_admin(interaction):
            return
        service = _games_wiring()
        if service is None:
            await interaction.response.send_message("Wiring indisponible.", ephemeral=True)
            return
        pool = await service.get_map_pool(self.pool_id)
        if pool is None:
            await interaction.response.send_message("Pool introuvable.", ephemeral=True)
            return
        remaining = tuple(mid for mid in pool.map_ids if mid != self.map_id)
        try:
            await service.update_map_pool(pool.id, map_ids=remaining)
        except Exception:
            logger.warning("POOL FLOW: remove from pool failed", exc_info=True)
            await interaction.response.send_message("Retrait impossible (voir les logs).", ephemeral=True)
            return
        await interaction.response.send_message(
            f"Map retirée de **{pool.name}** — la fiche du pool se met à jour à la prochaine sync.",
            ephemeral=True,
        )


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
            await interaction.response.send_message("Wiring indisponible.", ephemeral=True)
            return
        pool = await service.get_map_pool(self.pool_id)
        if pool is None:
            await interaction.response.send_message("Pool introuvable.", ephemeral=True)
            return
        maps = [m for m in await service.list_maps(pool.game_key) if m.id not in pool.map_ids]
        view = PoolMapPickerView(pool, maps, page=0)
        await interaction.response.send_message(
            f"Ajouter une map à **{pool.name}** — page 1/{view.pages}",
            view=view,
            ephemeral=True,
        )


class PoolMapPickerView(discord.ui.View):
    """Ephemeral paginated picker: pick one map to add to the pool."""

    PAGE_SIZE = 24

    def __init__(self, pool: Any, maps: list[Any], page: int = 0) -> None:
        super().__init__(timeout=180)
        self.pool = pool
        self.maps = maps
        self.pages = max(1, (len(maps) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        self.page = max(0, min(page, self.pages - 1))
        chunk = maps[self.page * self.PAGE_SIZE : (self.page + 1) * self.PAGE_SIZE]
        select: discord.ui.Select[Any] = discord.ui.Select(
            placeholder=f"{pool.name} — choisir une map",
            options=[discord.SelectOption(label=m.name, value=m.id) for m in chunk]
            or [discord.SelectOption(label="Aucune map disponible", value="none")],
        )
        select.callback = self._on_pick  # type: ignore[method-assign]
        self.add_item(select)
        if self.page > 0:
            prev: discord.ui.Button[Any] = discord.ui.Button(label="<", style=discord.ButtonStyle.secondary)
            prev.callback = self._nav(self.page - 1)  # type: ignore[method-assign]
            self.add_item(prev)
        if self.page < self.pages - 1:
            nxt: discord.ui.Button[Any] = discord.ui.Button(label=">", style=discord.ButtonStyle.secondary)
            nxt.callback = self._nav(self.page + 1)  # type: ignore[method-assign]
            self.add_item(nxt)

    def _nav(self, page: int) -> Any:
        async def _go(interaction: discord.Interaction) -> None:
            await interaction.response.edit_message(
                content=f"Ajouter une map à **{self.pool.name}** — page {page + 1}/{self.pages}",
                view=PoolMapPickerView(self.pool, self.maps, page=page),
            )

        return _go

    async def _on_pick(self, interaction: discord.Interaction) -> None:
        children = [c for c in self.children if isinstance(c, discord.ui.Select)]
        map_id = (children[0].values or [""])[0] if children else ""
        service = _games_wiring()
        if service is None or map_id in ("", "none"):
            await interaction.response.edit_message(content="Sélection invalide.")
            return
        pool = await service.get_map_pool(self.pool.id)
        entry = await service.get_map(map_id)
        if pool is None or entry is None:
            await interaction.response.edit_message(content="Map ou pool introuvable.")
            return
        try:
            await service.update_map_pool(pool.id, map_ids=(*pool.map_ids, map_id))
        except Exception:
            logger.warning("POOL FLOW: add to pool failed", exc_info=True)
            await interaction.response.edit_message(content="Ajout impossible (voir les logs).")
            return
        await interaction.response.edit_message(
            content=f"**{entry.name}** ajoutée à **{pool.name}** — la fiche du pool se met à jour à la prochaine sync."
        )


def register_pool_flow_items(bot: discord.Client) -> None:
    """Register the flow's DynamicItems (called at every startup)."""
    bot.add_dynamic_items(MapAddToPoolButton)
    bot.add_dynamic_items(MapPoolPickerSelect)
    bot.add_dynamic_items(PoolRemoveMapButton)
    bot.add_dynamic_items(PoolAddMapButton)
