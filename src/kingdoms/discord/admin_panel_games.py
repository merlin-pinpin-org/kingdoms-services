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

from kingdoms.core.services.game_data import InvalidPoolStateError
from kingdoms.discord.commands_i18n import reply
from kingdoms.discord.ui.view_footer import add_view_footer

logger = logging.getLogger("kingdoms.games.admin_panel")

MOD_KEY = "games"
_NS = "admin:pin:modgames"
GAME_KEY = "aoe2"  # default context; the picker may switch it per view


async def _pool_admin_menu(pool_id: str, game_key: str) -> discord.ui.LayoutView:
    """Render the pool's admin menu (the post's two action lines)."""
    from kingdoms.discord.entity_admin_menu import build_entity_admin_menu

    return await build_entity_admin_menu("pool", pool_id, game_key, owned=True)


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
    view.add_item(actions)
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
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        try:
            # the guild's pools carry its id in theirs — 'public' is the
            # bot-admin-only scope, never a guild creation's default
            pool = await service.create_map_pool(
                self.game_key,
                str(self.name.value).strip(),
                map_ids=(),
                owner_guild_id=guild_id or None,
                is_public=False,
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
        # the pool's post link: the thread id is the message id of a
        # forum post's starter message (the link targets the channel id,
        # then the thread id)
        pool_link = ""
        fresh = await service.get_map_pool(pool.id)
        if fresh is not None and getattr(fresh, "forum_thread_id", None) and interaction.guild_id:
            pool_link = (
                f" — son post : https://discord.com/channels/{interaction.guild_id}/"
                f"{fresh.forum_thread_id}/{fresh.forum_thread_id}"
            )
        await interaction.edit_original_response(
            content=f"Pool **{pool.name}** créé{pool_link or ' — son post est dans le forum des map pools.'}"
        )



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
    interaction: discord.Interaction,
    pool: Any,
    *,
    answer_menu: bool = True,
    origin: Any = None,
    sync_members: bool = True,
) -> None:
    """After a pool mutation: edit the ORIGINAL menu in place, sync after.

    The ephemeral contract: the lifecycle buttons capture the menu's
    (webhook, message id) at click time (or their modal does) and THAT
    message is edited with the fresh view through the interaction
    webhook — an ephemeral answers 404 to the REST channel route, and
    a followup copy is the forbidden shape. The forum post follows
    through the refresh (the post is only a mirror of the database
    record), after the ack.

    ``sync_members=False`` skips the member map posts' refresh: a
    rename changes nothing on them (the pool links are raw URLs, no
    frozen label), so their PATCH would burn budget for nothing.
    """
    if answer_menu:
        view = await _pool_admin_menu(pool.id, pool.game_key)
        if origin is not None:
            from kingdoms.discord.ephemeral import edit_ephemeral

            landed = await edit_ephemeral(origin, view)
            if not landed:
                try:
                    await interaction.followup.send(
                        "Menu non rafraîchi — rouvre-le pour voir l'état actuel.",
                        ephemeral=True,
                    )
                except Exception:
                    logger.warning("pool menu fallback answer failed — best-effort", exc_info=True)
        elif interaction.response.is_done():
            await interaction.followup.send(view=view, ephemeral=True)
        else:
            await interaction.response.edit_message(view=view)
    if interaction.guild_id is not None:
        try:
            from kingdoms.discord.pools_forum import refresh_pool_post

            await refresh_pool_post(
                str(interaction.guild_id), interaction.client, pool, sync_members=sync_members
            )
        except Exception:
            logger.warning("pool transition: pool post refresh failed — best-effort", exc_info=True)


class GamesPoolRenameButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:pools:rename:(?P<pool_id>.+)",
):
    """Open the rename modal; the pool id rides the custom_id."""

    def __init__(self, pool_id: str) -> None:
        self.pool_id = pool_id
        super().__init__(
            discord.ui.Button(
                label="Renommer",
                emoji="\u270f",
                style=discord.ButtonStyle.secondary,
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
        from kingdoms.discord.ephemeral import EphemeralOrigin

        await interaction.response.send_modal(
            GamesPoolRenameModal(self.pool_id, origin=EphemeralOrigin.capture(interaction))
        )


class GamesPoolRenameModal(discord.ui.Modal):
    """The pool rename form."""

    def __init__(self, pool_id: str, origin: Any = None) -> None:
        self.pool_id = pool_id
        self.origin = origin
        super().__init__(title="Renommer le pool", timeout=None)
        self.name: discord.ui.TextInput[Any] = discord.ui.TextInput(label="New name", max_length=64, required=True)
        self.add_item(self.name)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Ack first, rename, sync, confirm (the sync is slow — never past the 3s window)."""
        await interaction.response.defer(ephemeral=True, thinking=True)
        service = _games_wiring()
        if service is None:
            await interaction.followup.send(
                await reply(interaction, "replies_shared.wiring_unavailable"), ephemeral=True
            )
            return
        try:
            pool = await service.update_map_pool(self.pool_id, name=str(self.name.value).strip())
        except Exception:
            logger.exception("GAMES ADMIN: pool rename failed")
            await interaction.followup.send("Renommage échoué (voir les logs).", ephemeral=True)
            return
        # confirm FIRST (the answer never waits on the channel's budget),
        # then the sync edits the post's title and layout right behind —
        # the rename is visible on the post within the same breath
        await interaction.followup.send(f"Pool renommé **{pool.name}**.", ephemeral=True)
        await _finish_pool_transition(
            interaction, pool, origin=self.origin, sync_members=False
        )


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
        if pool.archived_at is not None:
            await interaction.response.send_message(
                f"**{pool.name}** est d\u00e9j\u00e0 archiv\u00e9.", ephemeral=True
            )
            return
        # capture BEFORE the defer: the thinking answer is a NEW
        # ephemeral \u2014 the menu's (webhook, id) pair is the only way
        # back to the original message
        from kingdoms.discord.ephemeral import EphemeralOrigin

        origin = EphemeralOrigin.capture(interaction)
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            pool = await service.archive_map_pool(self.pool_id)
        except Exception as exc:
            await interaction.followup.send(
                f"Archivage \u00e9chou\u00e9 \u2014 {exc}", ephemeral=True
            )
            return
        # the ephemeral contract: the ORIGINAL menu edits in place with
        # the archived state's buttons \u2014 the confirm is a plain text
        # followup, never a copy of the view
        await interaction.followup.send(
            f"Pool **{pool.name}** archiv\u00e9 \u2014 inutilisable mais toujours visible. "
            "Son id reste r\u00e9serv\u00e9 tant qu'il n'est pas supprim\u00e9 totalement.",
            ephemeral=True,
        )
        await _finish_pool_transition(interaction, pool, answer_menu=True, origin=origin)





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
        service = _games_wiring()
        if service is None or not self.pool_id:
            await interaction.response.send_message(
                await reply(interaction, "replies_shared.wiring_unavailable"), ephemeral=True
            )
            return
        try:
            pool = await service.transition_map_pool(self.pool_id, "draft")
        except InvalidPoolStateError as exc:
            # the runtime transition check: the pool's state moved since
            # the menu rendered — the answer names the live state
            await interaction.response.send_message(
                f"Impossible \u2014 {exc}. Rouvre le menu pour voir ses actions.",
                ephemeral=True,
            )
            return
        except Exception:
            logger.exception("GAMES ADMIN: pool to-draft failed (%s)", self.pool_id)
            await interaction.response.send_message(
                await reply(interaction, "replies_shared.transition_failed"), ephemeral=True
            )
            return
        # same shape as the other lifecycle buttons: the menu edits in place
        await interaction.response.edit_message(view=await _pool_admin_menu(pool.id, pool.game_key))
        await interaction.followup.send(
            f"Pool **{pool.name}** repassé en édition.", ephemeral=True
        )
        await _finish_pool_transition(interaction, pool, answer_menu=False)


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
            pool = await service.transition_map_pool(pool.id, "published")
        except InvalidPoolStateError as exc:
            # the runtime transition check: the pool's state moved since
            # the menu rendered — the answer names the live state
            await interaction.response.send_message(
                f"Impossible \u2014 {exc}. Rouvre le menu pour voir ses actions.",
                ephemeral=True,
            )
            return
        except Exception:
            logger.exception("GAMES ADMIN: pool publish failed")
            await interaction.response.send_message(
                await reply(interaction, "replies_shared.transition_failed"), ephemeral=True
            )
            return
        await interaction.response.edit_message(view=await _pool_admin_menu(pool.id, pool.game_key))
        await interaction.followup.send(
            f"Pool **{pool.name}** publié.", ephemeral=True
        )
        await _finish_pool_transition(interaction, pool, answer_menu=False)


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
        """Open the exact-id confirmation modal (the definitive delete)."""
        from kingdoms.discord.maps_pool_flow import _guard_admin

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
        from kingdoms.discord.ephemeral import EphemeralOrigin

        await interaction.response.send_modal(
            PoolHardDeleteModal(pool, origin=EphemeralOrigin.capture(interaction))
        )


class PoolHardDeleteModal(discord.ui.Modal):
    """The definitive delete: type the pool's exact id to confirm.

    A mismatch refuses; a match hard-deletes \u2014 the record, the post
    (at the next sync) and the reserved id all go away.
    """

    def __init__(self, pool: Any, origin: Any = None) -> None:
        self.pool = pool
        self.origin = origin
        # the modal title carries the pool id; the label stays under
        # Discord's 45-char cap
        super().__init__(title=f"Supprimer {pool.id[:24]}", timeout=300)
        self.confirm: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label="Tape l'id exact du pool",
            max_length=100,
            required=True,
        )
        self.add_item(self.confirm)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Refuse on mismatch or a runtime state check; delete on the exact id."""
        service = _games_wiring()
        current = await service.get_map_pool(self.pool.id) if service is not None else None
        if current is None:
            await interaction.response.send_message(
                "Ce pool n'existe plus \u2014 aucune suppression \u00e0 faire.", ephemeral=True
            )
            return
        # the runtime transition check: the delete is an ARCHIVED pool's
        # action only — the state may have moved since the button click
        if current.state != "closed":
            await interaction.response.send_message(
                f"**{current.name}** n'est plus archiv\u00e9 (\u00e9tat : {current.state}) "
                "\u2014 la suppression d\u00e9finitive est r\u00e9serv\u00e9e aux pools archiv\u00e9s. "
                "Rouvre le menu pour voir ses actions.",
                ephemeral=True,
            )
            return
        typed = str(self.confirm.value or "").strip()
        if typed != current.id:
            await interaction.response.send_message(
                f"Refus\u00e9 \u2014 l'id ne correspond pas (`{typed}` \u2260 `{current.id}`). "
                "Aucune suppression effectu\u00e9e.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        service = _games_wiring()
        if service is None:
            await interaction.followup.send(
                await reply(interaction, "replies_shared.wiring_unavailable"), ephemeral=True
            )
            return
        try:
            await service.hard_delete_map_pool(self.pool.id)
        except Exception:
            logger.exception("GAMES ADMIN: pool hard delete failed")
            await interaction.followup.send(
                await reply(interaction, "replies_shared.action_failed"), ephemeral=True
            )
            return
        # confirm FIRST — the sync is best-effort and never eats the answer
        await interaction.followup.send(
            f"Pool **{self.pool.name}** supprim\u00e9 d\u00e9finitivement \u2014 l'id "
            f"`{self.pool.id}` redevient utilisable.",
            ephemeral=True,
        )
        # the origin menu's buttons are dead (the pool is gone): edit it
        # to a plain closed note \u2014 never a copy of the view, and never
        # a menu of actions on a deleted pool
        if self.origin is not None:
            from kingdoms.discord.ephemeral import edit_ephemeral
            from kingdoms.discord.maps_pool_flow import _text_answer

            await edit_ephemeral(self.origin, _text_answer(f"`{self.pool.id}` \u2014 supprim\u00e9."))
        await _finish_pool_transition(interaction, self.pool, answer_menu=False)



PAGE_SIZE = 25  # Discord's hard cap on select options


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


class GamesMapImportButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:maps:import:(?P<scope>global|guild):(?P<game_key>[a-z0-9_]+)",
):
    """Import one public map from Liquipedia by name."""

    def __init__(self, game_key: str) -> None:
        self.game_key = game_key
        super().__init__(
            discord.ui.Button(
                label="Importer des maps",
                emoji="\U0001f5fa\ufe0f",
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
        await interaction.response.send_modal(GamesBulkImportModal(self.game_key))


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



def register_games_admin_items(bot: discord.Client) -> None:
    """Register the living DynamicItems (called at every startup)."""
    bot.add_dynamic_items(
        GamesMapCreateButton,
        GamesPoolCreateButton,
        GamesPoolRenameButton,
        GamesPoolToDraftButton,
        GamesPoolArchiveButton,
        GamesPoolAddByIdButton,
        GamesPoolDuplicateButton,
        GamesMapImportButton,
        GamesPoolCopyButton,
        GamesPoolPublishButton,
        GamesPoolHardDeleteButton,
    )
