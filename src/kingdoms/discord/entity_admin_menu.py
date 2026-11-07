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

        await interaction.response.defer(ephemeral=True, thinking=False)
        if not await _guard_admin(interaction):
            return
        view = await build_entity_admin_menu(
            self.kind,
            self.entity_id,
            self.game_key,
            self.owned,
            guild_id=str(interaction.guild_id) if interaction.guild_id is not None else "",
            bot=interaction.client,
        )
        await interaction.edit_original_response(view=view)


def _pool_lifecycle_row(entity_id: str, pool: Any, state: str) -> Any:
    """Build the pool's lifecycle row per the designer's visibility matrix.

    Each button shows ONLY on its status (hidden otherwise, never
    greyed): edition -> publier, archiver; published -> editer,
    archiver; archived -> editer, publier, supprimer (never used only).
    """
    from kingdoms.core.services.game_data import (
        POOL_STATE_CLOSED,
        POOL_STATE_DRAFT,
        POOL_STATE_PUBLISHED,
    )
    from kingdoms.discord.admin_panel_games import (
        GamesPoolArchiveButton,
        GamesPoolHardDeleteButton,
        GamesPoolPublishButton,
        GamesPoolToDraftButton,
    )

    lifecycle: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    ever_used = pool is not None and bool(pool.ever_activated)
    if state == POOL_STATE_PUBLISHED:
        lifecycle.add_item(GamesPoolToDraftButton(entity_id))
    if state == POOL_STATE_DRAFT:
        lifecycle.add_item(GamesPoolPublishButton(entity_id))
    if state in (POOL_STATE_DRAFT, POOL_STATE_PUBLISHED):
        lifecycle.add_item(GamesPoolArchiveButton(entity_id))
    if state == POOL_STATE_CLOSED:
        lifecycle.add_item(GamesPoolToDraftButton(entity_id))
        lifecycle.add_item(GamesPoolPublishButton(entity_id))
        if not ever_used:
            lifecycle.add_item(GamesPoolHardDeleteButton(entity_id))
    return lifecycle


async def build_entity_admin_menu(
    kind: str,
    entity_id: str,
    game_key: str,
    owned: bool,
    guild_id: str = "",
    bot: Any = None,
) -> discord.ui.LayoutView:
    """Build the ephemeral management menu of one entity (state-greyed)."""
    from kingdoms.core.services.game_data import (
        MAX_POOL_MAPS,
        POOL_STATE_DRAFT,
    )
    from kingdoms.discord.maps_pool_flow import (
        MapAddToPoolButton,
        MapDeleteButton,
        MapEditButton,
        PoolMapsButton,
        _games_wiring,
    )
    from kingdoms.discord.ui.menu_template import MenuSpec, build_menu

    if kind == "map":
        # the admin menu IS the map card (owner direction, like the
        # pools): the same post shape, only the action row swaps. The
        # bare title-and-buttons menu lost the whole body — the
        # 2026-11-07 merlin report.
        service = _games_wiring()
        entry = await service.get_map(entity_id) if service is not None else None
        if entry is not None and guild_id and bot is not None:
            from kingdoms.discord.maps_forum import map_post_render

            buttons: list[Any] = [MapAddToPoolButton(entity_id)]
            if owned:
                buttons.append(MapEditButton(entity_id))
            buttons.append(MapDeleteButton(entity_id, game_key=game_key, owned_by_guild=owned))
            _, view = await map_post_render(
                entry, guild_id, bot, service, admin_buttons=tuple(buttons)
            )
            return view  # type: ignore[no-any-return]
        menu = build_menu(
            MenuSpec(
                title="⚙️ Gestion de la map",
                description=f"`{entity_id}`",
                view_id=f"panel:{entity_id}:admin",
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
        if pool is None:
            # a hard-deleted pool never renders a menu \u2014 the default
            # state would resurrect it as a draft editor
            from kingdoms.discord.ui.menu_template import MenuSpec, build_menu

            return build_menu(
                MenuSpec(
                    title="\u2699\ufe0f Gestion du map pool",
                    description=f"`{entity_id}` \u2014 introuvable (supprim\u00e9 ?).",
                    view_id="",
                    sections=(),
                )
            )
        state = pool.state
        # The admin view IS the pool card (owner direction 2026-11-06):
        # the same shape as the forum post — state header, the member
        # maps WITH their images — only the BUTTONS change. No
        # duplicated menu layout, no vanishing images.
        from kingdoms.discord.admin_panel_games import (
            GamesPoolRenameButton,
            _games_wiring,
        )
        from kingdoms.discord.pools_forum import _pool_maps, _pool_post_layout

        service = _games_wiring()
        guild_id = str(getattr(pool, "owner_guild_id", "") or "")
        maps = await _pool_maps(service, pool, guild_id) if service else []
        admin_buttons: list[Any] = []
        if state == POOL_STATE_DRAFT:
            maps_button = PoolMapsButton(entity_id)
            if len(getattr(pool, "map_ids", ()) or ()) >= MAX_POOL_MAPS:
                maps_button.item.disabled = True
            admin_buttons.append(maps_button)
            admin_buttons.append(GamesPoolRenameButton(entity_id))
        lifecycle = _pool_lifecycle_row(entity_id, pool, state)
        admin_buttons.append(lifecycle)
        return _pool_post_layout(pool, maps, editable=True, admin_buttons=tuple(admin_buttons))

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


async def _pool_maps_lines(pool: Any) -> list[str]:
    """Render the pool's maps as content lines (dead refs noted, never hidden)."""
    from kingdoms.discord.admin_panel_games import _games_wiring

    service = _games_wiring()
    if service is None:
        return []
    try:
        catalog = {m.id: m for m in await service.list_maps(str(getattr(pool, "game_key", "")))}
    except Exception:
        return []
    lines: list[str] = []
    for mid in getattr(pool, "map_ids", ()) or ():
        entry = catalog.get(mid)
        if entry is None:
            lines.append(f"- `{mid}` \u2014 introuvable (supprim\u00e9e ?)")
        else:
            lines.append(f"- **{entry.name}**")
    return lines


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



