"""The single ⚙️ Admin button of a public post — the designed rule.

A forum post (map, pool, civ) never carries action buttons: one
Admin button opens a permission-checked ephemeral management menu
whose buttons are **the existing persistent items** (add-to-pool,
edit, transition, delete…) — greyed by the entity's state. The
click guard is bot-admin (the catalog is platform-wide data).

- map: [Ajouter à un pool] [Modifier] [Supprimer]
- pool: [Ajouter une map] [Changer d'état] [Supprimer]
- civ: NO admin button — its data is cross-guild (the DM panel's
  sync owns it), the generic menu carried nothing useful.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import discord

logger = logging.getLogger("botforge.discord.entity_admin_menu")

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
        """Guard, then open the entity's contextual management menu.

        The menu answers as a NEW EPHEMERAL message — never an edit of
        the post the button rides on. A component ``defer`` is a
        DEFERRED_UPDATE of the carrying message (the ephemeral flag is
        ignored): ``edit_original_response`` then rewrote the PUBLIC
        POST with the admin buttons, visible to everyone (the
        2026-11-08 critical-path report — the admin controls live only
        in the clicker's ephemeral answer).
        """
        from botforge.discord.maps_pool_flow import _guard_admin

        if not await _guard_admin(interaction):
            return
        view = await build_entity_admin_bar(
            self.kind,
            self.entity_id,
            self.game_key,
            self.owned,
            guild_id=str(interaction.guild_id) if interaction.guild_id is not None else "",
            bot=interaction.client,
        )
        if interaction.response.is_done():
            await interaction.followup.send(view=view, ephemeral=True)
        else:
            await interaction.response.send_message(view=view, ephemeral=True)


def _pool_lifecycle_spec(entity_id: str, pool: Any, state: str) -> Any:
    """Build the pool's admin spec on the SDK's default lifecycle.

    The designer's visibility matrix rides the SDK (hidden, never
    greyed): the buttons are handed in, the SDK composes — and the
    hard delete is DISABLED BY DATA for an ever-used pool (a delete
    on a pool a real run consumed would orphan history).
    """
    from botforge.discord.admin_panel_games import (
        GamesPoolArchiveButton,
        GamesPoolHardDeleteButton,
        GamesPoolPublishButton,
        GamesPoolToDraftButton,
    )
    from botforge.discord.ui.entity_admin import (
        ARCHIVE,
        DELETE,
        EDIT,
        PUBLISH,
        EntityAdminSpec,
    )

    ever_used = pool is not None and bool(pool.ever_activated)
    return EntityAdminSpec(
        entity_id=entity_id,
        state=state,
        actions={
            EDIT: GamesPoolToDraftButton(entity_id),
            PUBLISH: GamesPoolPublishButton(entity_id),
            ARCHIVE: GamesPoolArchiveButton(entity_id),
            DELETE: GamesPoolHardDeleteButton(entity_id),
        },
        disabled=frozenset({DELETE}) if ever_used else frozenset(),
    )


async def build_entity_admin_bar(
    kind: str,
    entity_id: str,
    game_key: str,
    owned: bool,
    guild_id: str = "",
    bot: Any = None,
) -> discord.ui.LayoutView:
    """Build the ephemeral admin BAR of one entity — buttons only.

    The ⚙️ Admin click answers with the entity's admin BUTTON BAR (never
    a re-render of the post — the post re-renders only when its content
    changed; owner direction 2026-11-08). The bar rides the SDK's
    entity-admin seam (``ui/entity_admin.py``): the caller hands the
    buttons in, the SDK composes the rows and the
    ``panel:<entity_id>:admin`` footer — ALWAYS the last line.
    """
    from botforge.discord.maps_pool_flow import (
        MapAddToPoolButton,
        MapDeleteButton,
        MapEditButton,
    )
    from botforge.discord.ui.entity_admin import EntityAdminSpec, admin_panel_id, build_admin_bar
    from botforge.discord.ui.menu_template import MenuSpec, build_menu

    if kind == "map":
        row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
        row.add_item(MapAddToPoolButton(entity_id))
        if owned:
            row.add_item(MapEditButton(entity_id))
        row.add_item(MapDeleteButton(entity_id, game_key=game_key, owned_by_guild=owned))
        return build_admin_bar(EntityAdminSpec(entity_id=entity_id, extra_rows=(row,)))
    if kind == "pool":
        spec = await _pool_admin_spec(entity_id)
        if isinstance(spec, discord.ui.LayoutView):
            return spec  # the not-found / note menus
        return build_admin_bar(spec)

    # any other kind (a stale wire from a superseded surface): the
    # honest answer is that this entity carries no admin bar — the
    # civ posts dropped theirs (the menu had nothing useful)
    return build_menu(
        MenuSpec(
            title="⚙️ Admin",
            description=f"`{entity_id}`",
            view_id=admin_panel_id(entity_id),
            sections=(),
        )
    )


async def _pool_admin_spec(entity_id: str) -> Any | discord.ui.LayoutView:
    """Build the pool's admin spec: its own compose rows + the lifecycle.

    The per-entity override, clean: the pool's OWN actions (manage the
    maps, rename — drafts only) ride the spec's ``compose_rows``, the
    shared lifecycle rides the spec's ``actions`` — the SDK composes
    both, the consumer never touches the bar's shape.
    """
    from botforge.core.services.game_data import MAX_POOL_MAPS, POOL_STATE_DRAFT
    from botforge.discord.admin_panel_games import GamesPoolRenameButton
    from botforge.discord.maps_pool_flow import PoolMapsButton

    pool = await _resolve_pool(entity_id)
    if pool is None:
        # a hard-deleted pool never renders a bar — the default state
        # would resurrect it as a draft editor
        from botforge.discord.ui.menu_template import MenuSpec, build_menu

        return build_menu(
            MenuSpec(
                title="⚙️ Gestion du map pool",
                description=f"`{entity_id}` — introuvable (supprimé ?).",
                view_id="",
                sections=(),
            )
        )
    spec = _pool_lifecycle_spec(entity_id, pool, pool.state)
    if pool.state == POOL_STATE_DRAFT:
        compose: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
        maps_button = PoolMapsButton(entity_id)
        if len(getattr(pool, "map_ids", ()) or ()) >= MAX_POOL_MAPS:
            maps_button.item.disabled = True
        compose.add_item(maps_button)
        compose.add_item(GamesPoolRenameButton(entity_id))
        return spec.with_compose((compose,))
    return spec


async def _pool_maps_lines(pool: Any) -> list[str]:
    """Render the pool's maps as content lines (dead refs noted, never hidden)."""
    from botforge.discord.admin_panel_games import _games_wiring

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
    from botforge.discord.admin_panel_games import _games_wiring

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


__all__ = ["EntityAdminButton", "build_entity_admin_bar", "register_entity_admin_items"]



