"""The single ⚙️ Admin button of a public post — the designed rule.

A forum post (map, pool, civ) never carries action buttons: one
Admin button opens a permission-checked ephemeral management menu
whose buttons are **the existing persistent items** (add-to-pool,
edit, transition, delete…) — greyed by the entity's state. The
click guard is bot-admin (the catalog is platform-wide data).

- map: [Ajouter à un pool] [Modifier] [Supprimer]
- pool: [Ajouter une map] [Changer d'état] [Supprimer]
- civ: a disabled placeholder (cross-guild data lives in the DM
  panel's refresh) — the design's todo action bar.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import discord

logger = logging.getLogger("kingdoms.discord.entity_admin_menu")

_NS = "panel:admin"


class EntityAdminButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:(?P<kind>[a-z0-9_]+):(?P<entity_id>.+):(?P<game_key>[a-z0-9_]*):(?P<owned>[01])",
):
    """The single ⚙️ Admin button of a public post (persistent)."""

    def __init__(self, kind: str, entity_id: str, game_key: str = "", owned: bool = False) -> None:
        self.kind = kind
        self.entity_id = entity_id
        self.game_key = game_key
        self.owned = owned
        super().__init__(
            discord.ui.Button(
                label="Admin",
                emoji="⚙️",
                style=discord.ButtonStyle.secondary,
                custom_id=f"{_NS}:{kind}:{entity_id}:{game_key}:{1 if owned else 0}"[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> EntityAdminButton:
        """Rebuild the button from the wire."""
        del interaction, item
        return cls(
            match.group("kind"),
            match.group("entity_id"),
            match.group("game_key"),
            match.group("owned") == "1",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Guard, then open the entity's contextual management menu."""
        from kingdoms.discord.maps_pool_flow import _guard_admin

        await interaction.response.defer(ephemeral=True, thinking=True)
        if not await _guard_admin(interaction):
            return
        view = await build_entity_admin_menu(self.kind, self.entity_id, self.game_key, self.owned)
        await interaction.edit_original_response(view=view)


async def build_entity_admin_menu(
    kind: str, entity_id: str, game_key: str, owned: bool
) -> discord.ui.LayoutView:
    """Build the ephemeral management menu of one entity (state-greyed)."""
    from kingdoms.core.services.game_data import (
        MAX_POOL_MAPS,
        POOL_STATE_CLOSED,
        POOL_STATE_DRAFT,
        POOL_STATE_LABELS,
        POOL_STATE_PUBLISHED,
        POOL_TRANSITIONS,
    )
    from kingdoms.discord.maps_pool_flow import (
        MapAddToPoolButton,
        MapDeleteButton,
        MapEditButton,
        PoolAddMapButton,
        PoolRemoveMapsButton,
    )
    from kingdoms.discord.ui.menu_template import MenuSpec, build_menu

    if kind == "map":
        menu = build_menu(
            MenuSpec(
                title="⚙️ Gestion de la map",
                description=f"`{entity_id}`",
                view_id="",
                sections=(),
            )
        )
        row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
        row.add_item(MapAddToPoolButton(entity_id))
        if owned:
            row.add_item(MapEditButton(entity_id))
        row.add_item(MapDeleteButton(entity_id, game_key=game_key, owned_by_guild=owned))
        menu.add_item(row)
        return menu
    if kind == "pool":
        pool = await _resolve_pool(entity_id)
        state = getattr(pool, "state", POOL_STATE_DRAFT)
        from kingdoms.discord.admin_panel_games import (
            GamesPoolArchiveButton,
            GamesPoolHardDeleteButton,
            GamesPoolPublishButton,
            GamesPoolRenameButton,
        )
        from kingdoms.discord.ui.menu_template import MenuSpec, build_menu

        menu = build_menu(
            MenuSpec(
                title="\u2699\ufe0f Gestion du map pool",
                description=f"`{entity_id}` \u2014 {POOL_STATE_LABELS.get(state, state)}",
                view_id="",
                sections=(),
            )
        )
        # line 1: composition (add capped at the pool budget, remove, rename)
        composition: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
        add_button = PoolAddMapButton(entity_id)
        if pool is not None and len(getattr(pool, "map_ids", ()) or ()) >= MAX_POOL_MAPS:
            add_button.item.disabled = True
        composition.add_item(add_button)
        composition.add_item(PoolRemoveMapsButton(entity_id))
        composition.add_item(GamesPoolRenameButton(entity_id))
        menu.add_item(composition)
        # line 2: lifecycle (publish/archive/delete, disabled when the
        # transition is impossible from the pool's current state)
        targets = POOL_TRANSITIONS.get(state, ())
        lifecycle: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
        publish = GamesPoolPublishButton(entity_id)
        publish.item.disabled = POOL_STATE_PUBLISHED not in targets
        lifecycle.add_item(publish)
        archive = GamesPoolArchiveButton(entity_id)
        archive.item.disabled = POOL_STATE_CLOSED not in targets and state != POOL_STATE_PUBLISHED
        lifecycle.add_item(archive)
        delete = GamesPoolHardDeleteButton(entity_id)
        delete.item.disabled = state != POOL_STATE_DRAFT
        lifecycle.add_item(delete)
        menu.add_item(lifecycle)
        return menu

    # civ (and any future kind): the cross-guild data is managed through
    # the DM panel — a plain note keeps the surface honest
    menu = build_menu(
        MenuSpec(
            title="⚙️ Gestion",
            description=(
                f"`{entity_id}`\n\nCette donnée est cross-guild : "
                "elle se met à jour via le bouton de synchronisation du "
                "panel admin DM (raffraichir le contenu)."
            ),
            view_id="",
            sections=(),
        )
    )
    return menu


async def _resolve_pool(entity_id: str) -> Any:
    """Resolve one pool for the admin menu (None when unreachable)."""
    from kingdoms.discord.admin_panel_games import _games_wiring

    service = _games_wiring()
    if service is None:
        return None
    try:
        return await service.get_map_pool(entity_id)
    except Exception:
        return None


def register_entity_admin_items(bot: discord.Client) -> None:
    """Register the entity admin items on the client."""
    bot.add_dynamic_items(EntityAdminButton)


__all__ = ["EntityAdminButton", "build_entity_admin_menu", "register_entity_admin_items"]



