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
    import os

    if not os.environ.get("MONGO_URI"):
        return None
    try:
        from kingdoms.core.games.aoe2.seed import MongoAoE2Database
        from kingdoms.core.models.db import get_async_database
        from kingdoms.core.services.game_data import GameDataService

        return GameDataService(MongoAoE2Database(get_async_database()))
    except Exception:
        logger.warning("POOL FLOW: wiring failed", exc_info=True)
        return None


async def _guard_admin(interaction: discord.Interaction) -> bool:
    """Deny non-admins ephemerally; True when the clicker may proceed."""
    from kingdoms.discord.guards import require_admin

    bot = interaction.client
    admins = getattr(getattr(bot, "status_service", None), "bot_admins", ())
    roles = getattr(bot, "roles_service", None)
    return await require_admin(interaction, admins, roles, denied_message="Réservé aux admins.")


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
                emoji="+",
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

    def __init__(self, map_id: str, pool_id: str) -> None:
        self.map_id = map_id
        self.pool_id = pool_id
        super().__init__(
            discord.ui.Button(
                label="Retirer du pool",
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


def register_pool_flow_items(bot: discord.Client) -> None:
    """Register the flow's DynamicItems (called at every startup)."""
    bot.add_dynamic_items(MapAddToPoolButton)
    bot.add_dynamic_items(MapPoolPickerSelect)
    bot.add_dynamic_items(PoolRemoveMapButton)
