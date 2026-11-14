"""The games admin section: maps and map pools, per game (core, #133).

Registered into the /admin panel's Mods select under the ``games`` key:
the section manages the game catalogs — the maps (archive = disable,
the maps forum sync picks it up) and the map pools (create, rename,
edit the map list, archive). Everything goes through
the catalog service (validated + audited); the section only renders
and collects input.

Namespace discipline: every component rides ``admin:pin:modgames:``
- deliberately NOT under ``admin:pin:mod:`` (the routing select's
prefix); one custom_id, one dispatch path, restart proof. Every
sub-view carries the back button.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

import discord

from botforge.core.services.game_data import InvalidPoolStateError
from botforge.discord.commands_i18n import reply
from botforge.discord.ui.view_footer import add_view_footer

logger = logging.getLogger("botforge.games.admin_panel")

MOD_KEY = "games"
_NS = "admin:pin:modgames"
GAME_KEY = "aoe2"  # default context; the picker may switch it per view


async def _pool_admin_menu(pool_id: str, game_key: str) -> discord.ui.LayoutView:
    """Render the pool's admin menu (the post's two action lines)."""
    from botforge.discord.entity_admin_menu import build_entity_admin_bar

    return await build_entity_admin_bar("pool", pool_id, game_key, owned=True)


def _games_wiring() -> Any | None:
    """Build the game-data wiring; None when Mongo is not configured."""
    from botforge.discord.wiring import build_games_service

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
    from botforge.discord.commands_i18n import reply_locale

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
    actions.add_item(GamesMapOrphansButton(game_key))
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
        from botforge.discord.maps_pool_flow import _guard_admin

        if not await _guard_admin(interaction):
            return
        if self.global_scope:
            from botforge.discord.guards import is_bot_admin

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

    async def on_submit(self, interaction: discord.Interaction) -> None:  # noqa: C901
        """Create the map through the service, confirm.

        Guild admins create guild-local maps (``owner_guild_id``); only
        bot admins may create global ones (``None`` scope), so a global
        map is modifiable by admins alone.
        """
        # DEFER FIRST: the Liquipedia enrichment can take minutes (the
        # 429 backoffs); an interaction that answers after ~3 s shows
        # 'the application did not respond' (the merlin Socotra import).
        await interaction.response.defer(ephemeral=True, thinking=True)
        service = _games_wiring()
        if service is None:
            await interaction.followup.send(
                await reply(interaction, "replies_shared.wiring_unavailable"), ephemeral=True
            )
            return
        name = str(self.name.value or "").strip()
        if not name:
            await interaction.followup.send(
                await reply(interaction, "replies_shared.name_required"), ephemeral=True
            )
            return
        from botforge.discord.guards import is_bot_admin

        admins = getattr(getattr(interaction.client, "status_service", None), "bot_admins", ())
        guild_id = str(interaction.guild_id) if interaction.guild_id is not None else None
        is_bot_admin_click = is_bot_admin(getattr(interaction.user, "id", None), tuple(admins))
        owner_guild_id = None if is_bot_admin_click and self.global_scope else guild_id
        description = str(self.description.value or "").strip()
        filename = str(self.filename.value or "").strip() or name
        seed = None
        circuit_note = ""
        if owner_guild_id is not None:
            from botforge.aoe2.map_seed import LiquipediaCircuitOpenError, LiquipediaUnconfiguredError

            try:
                client = _game_seed_client()
                seed = await client.get_map_seed(name) if client is not None else None
            except LiquipediaUnconfiguredError:
                seed = None
            except LiquipediaCircuitOpenError as exc:
                # the API is rate-limiting: the map is still created with
                # the admin's fields, the enrichment can be redone later
                circuit_note = (
                    f"\n\u26a0\ufe0f Liquipedia limite les appels (429) — "
                    f"l'enrichissement est en pause. {exc}"
                )
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
            if seed is not None:
                from botforge.aoe2.map_seed import map_content_doc
                from botforge.discord.content_posts import content_service

                content = content_service()
                if content is not None:
                    # the full doc — a seed without a resolved image
                    # still carries the traits, tags and gallery (the
                    # 2026-11-07 no-image lesson, applied to imports too)
                    await content.content_store(map_content_doc(entry.id, entry.name, seed))
        except Exception:
            logger.exception("GAMES ADMIN: map creation failed")
            await interaction.followup.send(
                await reply(interaction, "replies_shared.import_failed"), ephemeral=True
            )
            return
        # The confirmation IS the post's exact shape (owner direction
        # 2026-11-06): the admin sees what the forum post looks like.
        try:
            from botforge.discord.maps_forum import map_post_render

            _content, view = await map_post_render(
                entry, str(interaction.guild_id or ""), interaction.client, service
            )
            await interaction.followup.send(
                f"✅ Map **{entry.name}** créée — voici son post "
                f"(il apparaîtra dans le forum maps).{circuit_note}",
                view=view,
                ephemeral=True,
            )
        except Exception:
            logger.warning("map post preview failed — the plain confirmation serves", exc_info=True)
            await interaction.followup.send(
                f"Map **{entry.name}** creee - elle apparaitra dans le forum maps.{circuit_note}",
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
        """Guard, then open the creation modal."""
        from botforge.discord.maps_pool_flow import _guard_admin

        if not await _guard_admin(interaction):
            return
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
            from botforge.discord.pools_forum import sync_pools_forum

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
        # The confirmation carries the pool's post content (owner direction
        # 2026-11-06): the admin sees the pool exactly as its forum post
        # renders.
        try:
            from botforge.discord.pools_forum import _pool_post_content

            maps = [
                {"name": name}
                for name in await service.resolve_pool_map_ids(
                    await service.get_map_pool(pool.id) or pool
                )
            ]
            preview = _pool_post_content(pool, maps)
            await interaction.edit_original_response(
                content=(
                    f"Pool **{pool.name}** créé{pool_link} — son post :\n\n{preview}"
                )
            )
        except Exception:
            logger.warning("pool post preview failed — the plain confirmation serves", exc_info=True)
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
        from botforge.discord.maps_pool_flow import PoolAddMapByIdModal, _guard_admin

        if not await _guard_admin(interaction):
            return
        await interaction.response.send_modal(PoolAddMapByIdModal(self.pool_id))


async def _finish_pool_transition(
    interaction: discord.Interaction,
    pool: Any,
    *,
    sync_members: bool = True,
) -> None:
    """After a pool mutation: refresh the pool's mirror post.

    The menu was already edited in place by the caller (the ephemeral
    contract: the click's own response edits it — a followup copy is
    the forbidden shape). The forum post follows through the targeted
    refresh (the post is only a mirror of the database record), after
    the ack.

    ``sync_members=False`` skips the member map posts' refresh: a
    rename changes nothing on them (the pool links are raw URLs, no
    frozen label), so their PATCH would burn budget for nothing.
    """
    if interaction.guild_id is not None:
        try:
            from botforge.discord.pools_forum import refresh_pool_post

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
        """Guard, then open the rename modal."""
        from botforge.discord.maps_pool_flow import _guard_admin

        if not await _guard_admin(interaction):
            return
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
        """Ack first, rename, sync, confirm (the sync is slow — never past the 3s window)."""
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
            await interaction.response.send_message(
                "Renommage échoué (voir les logs).", ephemeral=True
            )
            return
        # the modal was launched from the menu's component: edit_message
        # edits THE MENU (the only route to an ephemeral), the confirm
        # rides the followup, the slow mirror work comes last
        await interaction.response.edit_message(view=await _pool_admin_menu(pool.id, pool.game_key))
        await interaction.followup.send(f"Pool renommé **{pool.name}**.", ephemeral=True)
        await _finish_pool_transition(interaction, pool, sync_members=False)


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
        from botforge.discord.maps_pool_flow import _guard_admin

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
        # the ephemeral contract, the reliable shape: the click's own
        # response edits the menu (a webhook from ANOTHER interaction
        # cannot \u2014 404). The archive is one Mongo write, well inside
        # the 3s window; the slow mirror work rides the followups.
        try:
            pool = await service.archive_map_pool(self.pool_id)
        except Exception as exc:
            await interaction.response.send_message(
                f"Archivage \u00e9chou\u00e9 \u2014 {exc}", ephemeral=True
            )
            return
        await interaction.response.edit_message(view=await _pool_admin_menu(pool.id, pool.game_key))
        await interaction.followup.send(
            f"Pool **{pool.name}** archiv\u00e9 \u2014 inutilisable mais toujours visible. "
            "Son id reste r\u00e9serv\u00e9 tant qu'il n'est pas supprim\u00e9 totalement.",
            ephemeral=True,
        )
        await _finish_pool_transition(interaction, pool)





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
        from botforge.discord.maps_pool_flow import _guard_admin

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
        from botforge.discord.maps_pool_flow import _guard_admin

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
        await _finish_pool_transition(interaction, pool)


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
        from botforge.discord.maps_pool_flow import _guard_admin

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
        await interaction.response.send_modal(PoolHardDeleteModal(pool))


class PoolHardDeleteModal(discord.ui.Modal):
    """The definitive delete: type the pool's exact NAME to confirm.

    The name is what the admin reads on the card (the id rides the
    footer); a mismatch refuses, a match hard-deletes \u2014 the record,
    the post (at the next sync) and the reserved id all go away.
    """

    def __init__(self, pool: Any) -> None:
        self.pool = pool
        # the modal title carries the pool id; the label stays under
        # Discord's 45-char cap
        super().__init__(title=f"Supprimer {str(pool.name)[:32]}", timeout=300)
        self.confirm: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label="Tape le nom exact du pool",
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
        if typed != current.name:
            await interaction.response.send_message(
                f"Refus\u00e9 \u2014 le nom ne correspond pas (`{typed}` \u2260 `{current.name}`). "
                "Aucune suppression effectu\u00e9e.",
                ephemeral=True,
            )
            return
        service = _games_wiring()
        if service is None:
            await interaction.response.send_message(
                await reply(interaction, "replies_shared.wiring_unavailable"), ephemeral=True
            )
            return
        try:
            await service.hard_delete_map_pool(self.pool.id)
        except Exception:
            logger.exception("GAMES ADMIN: pool hard delete failed")
            await interaction.response.send_message(
                await reply(interaction, "replies_shared.action_failed"), ephemeral=True
            )
            return
        # the modal was launched from the menu's component: edit_message
        # closes THE MENU (its buttons are dead \u2014 the pool is gone)
        from botforge.discord.maps_pool_flow import _text_answer

        await interaction.response.edit_message(
            view=_text_answer(f"`{self.pool.id}` \u2014 supprim\u00e9.")
        )
        await interaction.followup.send(
            f"Pool **{self.pool.name}** supprim\u00e9 d\u00e9finitivement \u2014 l'id "
            f"`{self.pool.id}` redevient utilisable.",
            ephemeral=True,
        )
        # the post is a mirror \u2014 but the pool no longer resolves, so the
        # sync cannot find it: delete the thread explicitly (the stored
        # link knows it), best-effort
        if interaction.guild_id is not None and str(getattr(self.pool, "forum_thread_id", "") or ""):
            try:
                # a forum thread is NOT in the guild channel cache \u2014 the
                # REST fetch is the reliable route (get_channel returns
                # None for a thread it has not seen)
                thread = await interaction.client.fetch_channel(int(self.pool.forum_thread_id))
                if isinstance(thread, discord.Thread):
                    await thread.delete(reason=f"botforge: pool {self.pool.id} hard-deleted")
            except Exception:
                logger.warning("pool post delete after hard delete failed \u2014 best-effort", exc_info=True)
        await _refresh_member_map_posts_after_delete(interaction, self.pool)



async def _refresh_member_map_posts_after_delete(
    interaction: discord.Interaction, pool: Any
) -> None:
    """Re-render the member maps' posts after a hard delete (best-effort).

    The deleted pool's links must leave the map posts; the maps
    themselves may belong to other pools \u2014 their posts re-render.
    """
    if interaction.guild_id is None:
        return
    try:
        from botforge.discord.maps_forum import refresh_map_posts

        await refresh_map_posts(
            interaction.client, str(interaction.guild_id), list(pool.map_ids or ())
        )
    except Exception:
        logger.warning("member map refresh after pool delete failed \u2014 best-effort", exc_info=True)


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
        """Guard, then duplicate under a fresh name."""
        from botforge.discord.maps_pool_flow import _guard_admin

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
        from botforge.discord.maps_pool_flow import _guard_admin

        if not await _guard_admin(interaction):
            return
        await interaction.response.send_modal(GamesBulkImportModal(self.game_key))


class GamesMapOrphansButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:maps:orphans:(?P<game_key>[a-z0-9_]+)",
):
    """List the orphan maps (no forum post) with their admin menu (#259).

    A map whose import failed after the record landed but whose
    rollback failed too has NO forum post \u2014 and the map's admin
    menu only lives on its post. This button is the admin-visible
    orphan list: one ephemeral pass, each orphan with its admin
    button (the menu that carries the delete).
    """

    def __init__(self, game_key: str) -> None:
        self.game_key = game_key
        super().__init__(
            discord.ui.Button(
                label="Orphelines",
                emoji="\U0001f9f9",
                style=discord.ButtonStyle.secondary,
                custom_id=f"{_NS}:maps:orphans:{game_key}"[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> GamesMapOrphansButton:
        """Rebuild the item from the wire (game key from the custom_id)."""
        del interaction, item
        return cls(match.group("game_key"))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Guard, list the orphans, hand each its admin menu entry."""
        from botforge.discord.maps_pool_flow import _guard_admin

        await interaction.response.defer(ephemeral=True, thinking=False)
        if not await _guard_admin(interaction):
            return
        service = _games_wiring()
        if service is None:
            await interaction.edit_original_response(
                content=await reply(interaction, "replies_shared.wiring_unavailable")
            )
            return
        maps = await service.list_maps(self.game_key)
        orphans = [m for m in maps if m.archived_at is None and not m.forum_message_id]
        view = discord.ui.LayoutView(timeout=None)
        title = f"# Maps orphelines \u2014 {len(orphans)}"
        if not orphans:
            view.add_item(
                discord.ui.Container(
                    discord.ui.TextDisplay(f"{title}\nAucune map orpheline \u2014 tout a son post.")
                )
            )
        for entry in orphans[:20]:
            owned = entry.owner_guild_id is not None
            row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
            from botforge.discord.entity_admin_menu import EntityAdminButton

            row.add_item(
                EntityAdminButton("map", entry.id, game_key=self.game_key, owned=owned)
            )
            view.add_item(
                discord.ui.Container(
                    discord.ui.TextDisplay(f"**{entry.name}** \u2014 `{entry.id}`"),
                    row,
                )
            )
        await interaction.edit_original_response(view=view)


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
    final: Callable[[str], Awaitable[None]] | None = None,
) -> None:
    """Store the imported map's content and sync its forum post (#259).

    Transactional tail: any failure after the record landed rolls the
    record back — no orphaned "name taken" residue. When the rollback
    itself fails, the popup says the map is orphaned and how to clean
    it (delete from the Maps menu, no forum thread needed).
    """
    try:
        from botforge.discord.content_posts import content_service

        content = content_service()
        if content is not None:
            from botforge.aoe2.map_seed import map_content_doc

            await content.content_store(map_content_doc(entry.id, entry.name, seed))
        from botforge.discord.maps_forum import sync_maps_forum

        if interaction.guild_id is not None:
            await sync_maps_forum(str(interaction.guild_id), game_key, interaction.client)
        # The answer IS the post (owner direction 2026-11-06): the view
        # plus the created post's link — never a bare "import réussi".
        try:
            from botforge.discord.maps_forum import map_post_render

            _content, view = await map_post_render(
                entry, str(interaction.guild_id or ""), interaction.client, service
            )
            fresh = await service.get_map(entry.id)
            thread_id = str(getattr(fresh, "forum_message_id", "") or "")
            link = (
                f"\n<https://discord.com/channels/{interaction.guild_id}/{thread_id}/{thread_id}>"
                if interaction.guild_id and thread_id.isdigit()
                else ""
            )
            await interaction.followup.send(
                f"\u2705 **{entry.name}** import\u00e9e \u2014 son post :{link}",
                view=view,
                ephemeral=True,
            )
        except Exception:
            logger.warning("map import preview failed — best-effort", exc_info=True)
    except Exception as exc:
        logger.exception("GAMES ADMIN: map import tail failed — rolling back %s", entry.id)
        rolled_back = False
        try:
            await service.delete_map(entry.id)
            rolled_back = True
        except Exception:
            logger.exception("GAMES ADMIN: rollback failed for %s", entry.id)
        if rolled_back:
            message = (
                f"Import échoué à l'étape « contenu/forum » ({exc}) — la map a été supprimée, "
                "réessaie."
            )
        else:
            message = (
                f"Import échoué à l'étape « contenu/forum » ({exc}) et le nettoyage a échoué : "
                f"la map **{entry.name}** (`{entry.id}`) est orpheline — supprime-la via le "
                "bouton « Orphelines » de la section Maps puis réessaie."
            )
        if final is not None:
            await final(message)
        else:
            await interaction.followup.send(message, ephemeral=True)
        return
    fresh = await service.get_map(entry.id)
    post_link = ""
    if fresh is not None and fresh.forum_message_id and interaction.guild_id is not None:
        post_link = (
            f" — son post : https://discord.com/channels/{interaction.guild_id}/"
            f"{fresh.forum_message_id}/{fresh.forum_message_id}"
        )
    message = f"Map **{entry.name}** importée{post_link or ' — son post est dans le forum maps.'}"
    if final is not None:
        await final(message)
        return
    await interaction.followup.send(
        message,
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

    async def on_submit(self, interaction: discord.Interaction) -> None:  # noqa: C901
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
        # The long-running action pattern (skills/discord-ui): announce
        # WHAT runs and that the result lands in this very message \u2014
        # the Liquipedia seed is rate-limited (>= 2 s per request, 3-4
        # requests per map), past Discord's 3-second window.
        await interaction.response.defer(ephemeral=True, thinking=True)
        progress: Any = await interaction.followup.send(  # type: ignore[func-returns-value]
            f"\U0001F504 Import de « {name} » en cours \u2014 interrogation de Liquipedia… "
            "Le r\u00e9sultat sera post\u00e9 dans ce message.",
            ephemeral=True,
        )
        from botforge.aoe2.map_seed import LiquipediaUnconfiguredError
        try:
            client = _game_seed_client()
            seed = await client.get_map_seed(name) if client is not None else None
        except LiquipediaUnconfiguredError:
            await interaction.followup.edit_message(
                progress.id,
                content=await reply(interaction, "replies_shared.liquipedia_unconfigured"),
            )
            return
        if seed is None:
            await interaction.followup.edit_message(
                progress.id,
                content=f'Aucune page Liquipedia pour "{name}" \u2014 cr\u00e9e la map localement.',
            )
            return
        from botforge.core.services.game_data import NameTakenError

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
            # The verdict carries the EXISTING map's post (owner direction
            # 2026-11-06): the admin sees what already exists, exactly as
            # it renders in the forum.
            existing = None
            try:
                from botforge.core.ids import map_entry_id

                existing = await service.get_map(map_entry_id(self.game_key, seed.name, None))
            except Exception:
                logger.debug("existing map lookup failed", exc_info=True)
            if existing is not None:
                try:
                    from botforge.discord.maps_forum import map_post_render

                    _content, view = await map_post_render(
                        existing, str(interaction.guild_id or ""), interaction.client, service
                    )
                    await interaction.followup.edit_message(
                        progress.id,
                        content=f"⚠️ {error} — la map existante :",
                    )
                    await interaction.followup.send(view=view, ephemeral=True)
                    return
                except Exception:
                    logger.debug("existing map preview failed", exc_info=True)
            await interaction.followup.edit_message(
                progress.id,
                content=f"Import refusé — {error}. Supprime ou archive la map existante d'abord.",
            )
            return
        except Exception as exc:
            logger.exception("GAMES ADMIN: map creation failed")
            await interaction.followup.edit_message(
                progress.id,
                content=f"Import échoué à l'étape « création » : {exc}",
            )
            return

        async def _final(summary: str) -> None:
            await interaction.followup.edit_message(progress.id, content=summary)

        await _finish_map_import(interaction, service, self.game_key, entry, seed, _final)


ProgressCallback = Callable[[str, bool, str], Awaitable[None]]
"""Per-line progress reporter: (page_name, ok, reason) — awaited live."""


async def _bulk_import_maps(
    service: Any,
    game_key: str,
    parsed: list[Any],
    on_result: ProgressCallback | None = None,
) -> tuple[list[Any], list[tuple[str, str]]]:
    """Import every missing map, best-effort; (created, failures).

    Existing non-archived maps are skipped (re-import = no-op). One
    map failing never stops the batch: every line runs to the end,
    the successes stay in the catalog, and each failure comes back
    with its reason — the caller reports both sides. A partial
    import is an accepted outcome, not a state to undo. ``on_result``
    is awaited after every line so the caller can stream the batch's
    progress in real time.
    """

    async def _report(name: str, ok: bool, reason: str = "") -> None:
        if on_result is not None:
            await on_result(name, ok, reason)

    from botforge.aoe2.map_seed import LiquipediaCircuitOpenError

    created: list[Any] = []
    failures: list[tuple[str, str]] = []
    for index, p in enumerate(parsed):
        try:
            from botforge.core.ids import map_entry_id

            existing = await service.get_map(map_entry_id(game_key, p.page_name, None))
            if existing is not None and existing.archived_at is None:
                await _report(p.page_name, True, "déjà présente")
                continue
            client = _game_seed_client()
            seed = await client.get_map_seed(p.page_name) if client is not None else None
            if seed is None:
                failures.append((p.page_name, "aucune page Liquipedia"))
                await _report(p.page_name, False, "aucune page Liquipedia")
                continue
            entry = await service.create_map(
                game_key,
                seed.name,
                filename=seed.name.replace(" ", "_").lower(),
                description=seed.description,
                resource_url=seed.resource_url,
                owner_guild_id=None,
                map_type=seed.map_type,
            )
            from botforge.discord.content_posts import content_service

            content = content_service()
            if content is not None:
                from botforge.aoe2.map_seed import map_content_doc

                await content.content_store(map_content_doc(entry.id, entry.name, seed))
            created.append(entry)
            await _report(p.page_name, True)
        except LiquipediaCircuitOpenError as exc:
            # The API is rate-limiting this IP and the circuit is open:
            # the remaining lines are REPORTED, not attempted — the batch
            # never turns into a burst against a banned upstream.
            failures.append((p.page_name, f"Liquipedia limite les appels (429) — {exc}"))
            await _report(p.page_name, False, "Liquipedia limite les appels (429), réessaie plus tard")
            for rest in parsed[index + 1 :]:
                failures.append((rest.page_name, "reporté — Liquipedia en pause (429)"))
                await _report(rest.page_name, False, "reporté — Liquipedia en pause (429)")
            break
        except Exception as exc:
            logger.exception("GAMES ADMIN: bulk import failed on %s", p.page_name)
            failures.append((p.page_name, str(exc)))
            await _report(p.page_name, False, str(exc))
    return created, failures


async def _finish_bulk_import(
    interaction: discord.Interaction,
    service: Any,
    created: list[Any],
    pool_id: str,
    final: Callable[[str], Awaitable[None]] | None = None,
) -> None:
    """Associate the batch to its pool, sync the forum, confirm.

    ``final`` is the live report's closer: when given, the closing
    summary goes through it (the one message the admin watched) instead
    of a fresh generic confirmation.
    """
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
        from botforge.discord.maps_forum import sync_maps_forum

        if interaction.guild_id is not None:
            game = str(created[0].game_key) if created else "aoe2"
            await sync_maps_forum(str(interaction.guild_id), game, interaction.client)
    except Exception:
        logger.warning("GAMES ADMIN: forum sync after bulk import failed — best-effort")
    target = f" et associées au pool `{pool_id}`" if pool_id else ""
    summary = (
        f"\U0001f4e5 Import termin\u00e9 \u2014 {len(created)} map(s) import\u00e9e(s){target}.\n"
        + "".join(f"\u2705 {entry.name}\n" for entry in created)
        + "Les posts arrivent dans le forum maps."
    )
    if final is not None:
        await final(summary)
    else:
        await interaction.followup.send(summary, ephemeral=True)
    # One ephemeral PER CREATED map, carrying the post's exact shape (owner
    # direction 2026-11-06): the admin sees every map as its forum post
    # renders — the same builder the sync uses.
    for entry in created:
        try:
            from botforge.discord.maps_forum import map_post_render

            _content, view = await map_post_render(
                entry, str(interaction.guild_id or ""), interaction.client, service
            )
            # Components V2 forbids a `content` alongside the view (the
            # 2026-11-08 50035 silent-preview failures) — the banner
            # rides INSIDE the view, as its first text display
            banner: discord.ui.TextDisplay[Any] = discord.ui.TextDisplay(
                f"\u2705 **{entry.name}** import\u00e9e \u2014 son post :"
            )
            # discord.py 2.7 LayoutView has no public insert-at-index:
            # rebuild the children with the banner first (children is a
            # copy, and clear_items resets the row weights).
            items = [banner, *view.children]
            view.clear_items()
            for item in items:
                view.add_item(item)
            await interaction.followup.send(view=view, ephemeral=True)
        except Exception:
            logger.warning(
                "bulk import: the per-map preview failed for %s \u2014 best-effort",
                entry.name,
                exc_info=True,
            )


async def _bulk_import_live_report(
    interaction: discord.Interaction, total: int
) -> tuple[ProgressCallback, Callable[[str], Awaitable[None]]]:
    """Stream the batch's progress through one ephemeral message.

    Returns ``(on_result, final)``. The acknowledgement ("request
    submitted, N lines queued") is posted right here — the user's
    request is confirmed the moment it is queued; ``on_result`` is
    awaited after every line and appends that line's outcome —
    success or failure, with its reason — to the live message;
    ``final`` replaces it with the closing summary. The admin watches
    the import run in real time instead of waiting for one generic
    verdict at the end.
    """
    state: dict[str, Any] = {"msg": None, "lines": []}

    def _body() -> str:
        lines = "\n".join(state["lines"]) or "(en attente…)"
        return (
            f"\U0001f4e5 Demande soumise \u2014 {total} map(s) en file\n"
            f"{len(state['lines'])}/{total} trait\u00e9e(s) :\n{lines}"
        )

    async def _edit() -> None:
        msg = state["msg"]
        if msg is None:
            # followup.send answers the sent message (editable through
            # followup.edit_message) — typed Any: discord.py marks it None.
            sent: Any = await interaction.followup.send(  # type: ignore[func-returns-value]
                _body(), ephemeral=True
            )
            state["msg"] = sent
        else:
            await interaction.followup.edit_message(msg.id, content=_body())

    async def on_result(name: str, ok: bool, reason: str = "") -> None:
        """Append one check's outcome to the live progress message."""
        icon = "\u2705" if ok else "\u274c"
        suffix = f" \u2014 {reason}" if reason else ""
        state["lines"].append(f"{icon} {name}{suffix}")
        await _edit()

    await _edit()

    async def final(summary: str) -> None:
        """Replace the progress message with the final summary."""
        msg = state["msg"]
        if msg is None:
            await interaction.followup.send(summary, ephemeral=True)
        else:
            await interaction.followup.edit_message(msg.id, content=summary)

    return on_result, final


class GamesBulkImportModal(discord.ui.Modal):
    """The bulk Liquipedia import: one map per line (id / slug / URL).

    Missing maps are imported from Liquipedia and (optionally) all
    lines are associated to a target pool. Every line runs whatever its
    neighbours did: the successes stay, the failures are reported —
    no batch rollback on a partial import.
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
        """Guard, parse, confirm, then report the batch live, line by line."""
        from botforge.discord.maps_pool_flow import _guard_admin

        if not await _guard_admin(interaction):
            return
        # thinking=True: the Liquipedia seeding is slow by design (API
        # rate limits) — the user sees the import working instead of a
        # silent wait.
        await interaction.response.defer(ephemeral=True, thinking=True)
        from botforge.aoe2.bulk_import import parse_import_line

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

        on_result, final = await _bulk_import_live_report(interaction, len(parsed))
        created, failures = await _bulk_import_maps(service, self.game_key, parsed, on_result)
        if not created and failures:
            lines = "\n".join(f"\u274c {name} \u2014 {reason}" for name, reason in failures)
            await final(f"\u274c Aucune map import\u00e9e \u2014 {len(failures)} \u00e9chec(s) :\n{lines}")
            return
        if created:
            await _finish_bulk_import(interaction, service, created, self.pool_id, final)
            if failures:
                await interaction.followup.send(
                    "\u26a0\ufe0f "
                    + f"{len(failures)} \u00e9chec(s) d\u00e9taill\u00e9(s) dans le rapport ci-dessus ; "
                    + "les autres maps sont bien import\u00e9es.",
                    ephemeral=True,
                )


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
        from botforge.discord.maps_pool_flow import _guard_admin

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
        from botforge.discord.maps_pool_flow import _guard_admin

        if not await _guard_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=False)
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
            from botforge.discord.pools_forum import sync_pools_forum

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
        GamesMapOrphansButton,
        GamesPoolCopyButton,
        GamesPoolPublishButton,
        GamesPoolHardDeleteButton,
    )


def _game_seed_client() -> Any | None:
    """Return the game service's client for the map seed lookups (rule 4)."""
    import os

    from botforge.game_aoe2.client import GameDataClient

    uri = os.environ.get("GAME_AOE2_URI", "")
    return GameDataClient(uri) if uri else None
