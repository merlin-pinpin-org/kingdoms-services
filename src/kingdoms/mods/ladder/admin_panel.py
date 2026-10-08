"""The ladder mod's admin section, registered into the /admin panel (#133).

The section renders the ladder's admin surface in the panel's Mods
select: the current ladder (name, game, rating system, queue depth),
its settings, its seasons and its active map pool - plus the admin
config actions (settings modal, season create/activate, pool switch).
Every mutation goes through the domain services (validated + audited,
reference 9.5/9.6); the section only renders and collects input - the
click guards stay the panel's (admins only, at click time).

Namespace discipline: every component here rides
``admin:pin:modladder:`` - deliberately NOT under
``admin:pin:mod:`` whose prefix belongs to the sections routing
select (a template match on that prefix would steal the dispatch);
one custom_id, one dispatch path, restart proof, no captured state.
Every sub-view carries the back button.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import discord

from kingdoms.discord.admin_panel_mods import AdminModSection, register_admin_mod_section
from kingdoms.discord.mod_admin_channels import ModAdminPinInteraction
from kingdoms.mods.ladder.commands import build_ladder_wiring

logger = logging.getLogger("kingdoms.ladder.admin_panel")

MOD_KEY = "ladder"
_NS = "admin:pin:modladder"
GAME_KEY = "aoe2"


def _catalog(interaction: discord.Interaction | ModAdminPinInteraction) -> Any:
    """Resolve the message catalog through the running client."""
    return getattr(getattr(interaction, "client", None), "messages", None)


async def _locale(interaction: discord.Interaction | ModAdminPinInteraction) -> str:
    """Read the guild's locale (en fallback)."""
    logs = getattr(getattr(interaction, "client", None), "logs_service", None)
    if logs is None:
        return "en"
    try:
        guild_id = str(getattr(interaction, "guild_id", "") or "")
        locale: str = await logs.get_locale(guild_id)
        return locale
    except Exception:
        return "en"


def _t(catalog: Any, locale: str, key: str, **kwargs: Any) -> str:
    """Render a ladder admin catalog key with an English fallback."""
    if catalog is None:
        return key
    rendered: str = catalog.render(f"ladder.{key}", locale, **kwargs)
    return rendered

LADDERS_COLLECTION = "ladders"


def _now_ms() -> int:
    """Epoch milliseconds, the repo's timestamp convention."""
    import time

    return int(time.time() * 1000)


async def _resolve_ladder(
    interaction: discord.Interaction | ModAdminPinInteraction, wiring: Any
) -> dict[str, Any] | None:
    """Resolve the guild's ladder the same way the commands do."""
    service = wiring.service
    ladder_id = getattr(interaction.client, "_ladder_id", None)
    if ladder_id:
        found: dict[str, Any] | None = await service._db.find_entry(LADDERS_COLLECTION, ladder_id)
        if found is not None:
            return found
    guild_ref = str(interaction.guild_id) if interaction.guild_id else "guild:default"
    for owner in (f"guild:{interaction.guild_id}", guild_ref, "guild:default", "default"):
        found2: dict[str, Any] | None = await service._db.find_ladder_by_owner(owner, GAME_KEY)
        if found2 is not None:
            return found2
    return None


async def _resolved_ladder_id(interaction: discord.Interaction, wiring: Any) -> str:
    """Resolve the guild's ladder id (bot config first, owner fallback)."""
    ladder = await _resolve_ladder(interaction, wiring)
    return str(ladder["_id"]) if ladder else ""


def _client_ladder_id(interaction: discord.Interaction) -> str:
    """Return the bot's configured default ladder id (no await needed)."""
    return str(getattr(interaction.client, "_ladder_id", "") or "")


def _back_row() -> discord.ui.ActionRow[discord.ui.LayoutView]:
    """Build the back row every sub-view carries (main menu return)."""
    from kingdoms.discord.admin_panel_dynamic import PinBackButton

    row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    row.add_item(PinBackButton(label="Retour"))
    return row


def _selected_values(interaction: discord.Interaction) -> list[str]:
    """Read a select interaction's chosen values (payload-shape proof)."""
    data = interaction.data
    raw = getattr(data, "values", None) if data is not None else None
    if raw is None and isinstance(data, dict):
        raw = data.get("values")
    return list(raw) if isinstance(raw, (list, tuple)) else []


async def ladder_admin_entry(
    interaction: discord.Interaction | ModAdminPinInteraction, pinned: bool = False
) -> discord.ui.LayoutView:
    """Render the ladder admin section's view (snapshot + config actions).

    ``pinned`` renders the ladder-admin channel's entry: a pin lives in
    its own channel, so it carries no back button — every sub-view keeps
    its return to this entry.
    """
    wiring = build_ladder_wiring()
    view = discord.ui.LayoutView(timeout=None)
    blocks: list[Any] = [discord.ui.TextDisplay("# Ladder admin")]
    if wiring is None:
        blocks.append(discord.ui.TextDisplay("Ladder wiring indisponible : Mongo/Redis ne sont pas configurés."))
        view.add_item(discord.ui.Container(*blocks, accent_colour=discord.Colour(0x5865F2)))
        if not pinned:
            view.add_item(_back_row())
        return view

    ladder = await _resolve_ladder(interaction, wiring)
    if ladder is None:
        blocks.append(discord.ui.TextDisplay("Aucun ladder pour ce guild - cree-le ici."))
        view.add_item(discord.ui.Container(*blocks, accent_colour=discord.Colour(0x5865F2)))
        create_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
        create_row.add_item(LadderCreateButton())
        view.add_item(create_row)
        if not pinned:
            view.add_item(_back_row())
        return view

    ladder_id = str(ladder["_id"])
    settings = ladder.get("settings", {})
    players = await wiring.service._db.find_ladder_players(ladder_id)
    in_queue = sum(1 for p in players if p.get("in_queue"))
    seasons = await wiring.season_service.list_seasons(ladder_id) if wiring.season_service else []
    active = await wiring.season_service.get_active_season(ladder_id) if wiring.season_service else None
    pools = await wiring.game_data.list_map_pools(GAME_KEY)
    active_pool = next((p for p in pools if p.id == ladder.get("active_map_pool_id")), None)

    season_lines = []
    for s in seasons[-5:]:
        marker = "actif" if s.id == (active.id if active else "") else s.state
        season_lines.append(f"- {s.name} ({marker}) - pool {s.map_pool_id or 'aucun'}")
    blocks.extend(
        [
            discord.ui.Separator(),
            discord.ui.TextDisplay(
                f"**{ladder.get('name', '?')}** - jeu `{ladder.get('game_key', GAME_KEY)}`\n"
                f"Rating : `{settings.get('rating_system', 'elo')}` - "
                f"Joueurs : {len(players)} - En file : {in_queue}\n"
                f"Inscriptions : **{'ouvertes' if ladder.get('enrollments_open', True) else 'fermées'}** - "
                f"File : **{'en pause' if ladder.get('queue_paused', False) else 'active'}**"
            ),
            discord.ui.TextDisplay(
                f"**Pool actif** : {active_pool.name if active_pool else '-'}\n"
                + ("\n".join(season_lines) if season_lines else "_Aucune saison_")
            ),
        ]
    )
    enrolled = len(players)
    pool_ok = active_pool is not None
    season_ok = active is not None
    start_ok = enrolled >= 2 and pool_ok and season_ok
    checklist = [
        (" joueurs inscrits (2 minimum)", enrolled >= 2, f"{enrolled}/2"),
        (" map pool actif", pool_ok, (active_pool.name if active_pool is not None else "aucun")),
        (" saison en cours", season_ok, (active.name if active is not None else "aucune")),
    ]
    checklist_lines = []
    for label, ok, detail in checklist:
        emoji = "\u2705" if ok else "\u26d4"
        checklist_lines.append(f"{emoji} {label} : {detail}")
    blocks.append(discord.ui.TextDisplay("\n".join(checklist_lines)))

    actions: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    actions.add_item(LadderSettingsButton(ladder_id))
    actions.add_item(LadderSeasonButton(ladder_id))
    actions.add_item(LadderPoolButton(ladder_id))
    pick_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    pick_row.add_item(LadderPickStrategySelect())
    cycle: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    cycle.add_item(
        LadderEnrollButton("open" if not ladder.get("enrollments_open", True) else "close")
    )
    paused = bool(ladder.get("queue_paused", False))
    start_state = "pause" if not paused else "resume"
    start_button = LadderPauseButton(start_state)
    if start_state == "resume" and not start_ok:
        start_button.item.disabled = True
    cycle.add_item(start_button)
    view.add_item(discord.ui.Container(*blocks, accent_colour=discord.Colour(0x5865F2)))
    view.add_item(actions)
    view.add_item(pick_row)
    view.add_item(cycle)
    if not pinned:
        view.add_item(_back_row())
    return view


class LadderCreateButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:create",
):
    """Create the guild's ladder from the panel (no seed needed)."""

    def __init__(self) -> None:
        super().__init__(
            discord.ui.Button(
                label="Creer le ladder",
                style=discord.ButtonStyle.success,
                custom_id=f"{_NS}:create",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> LadderCreateButton:
        """Rebuild the item from the wire (state read at click time)."""
        del item, match
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the ladder-creation modal."""
        wiring = build_ladder_wiring()
        if wiring is None:
            await interaction.response.send_message("Ladder wiring indisponible.", ephemeral=True)
            return
        existing = await _resolve_ladder(interaction, wiring)
        if existing is not None:
            await interaction.response.send_message("Un ladder existe deja pour ce guild.", ephemeral=True)
            return
        await interaction.response.send_modal(LadderCreateModal())


class LadderCreateModal(discord.ui.Modal):
    """The ladder-creation form: name (owner ref from the bot config)."""

    def __init__(self) -> None:
        super().__init__(title="Creer le ladder", timeout=None)
        self.name: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label="Nom du ladder", max_length=100, required=True
        )
        self.add_item(self.name)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Create the ladder, then re-render the section's entry view."""
        wiring = build_ladder_wiring()
        if wiring is None:
            await interaction.response.send_message("Ladder wiring indisponible.", ephemeral=True)
            return
        owner_ref = str(getattr(interaction.client, "_ladder_id", "") or "")
        if owner_ref.startswith("ladder-aoe2-"):
            owner_ref = owner_ref[len("ladder-aoe2-"):]
        if not owner_ref:
            owner_ref = str(interaction.guild_id) if interaction.guild_id else ""
        try:
            ladder = await wiring.service.create_ladder(
                owner_ref, str(self.name.value).strip() or "Ladder", GAME_KEY, now=_now_ms()
            )
        except Exception:
            logger.exception("LADDER ADMIN: ladder creation failed")
            await interaction.response.send_message("Creation echouee (deja existant ?).", ephemeral=True)
            return
        await wiring.service._audit_record(
            "ladder.create", {"ladder_id": ladder.id, "name": ladder.name}
        )
        await interaction.response.edit_message(view=await ladder_admin_entry(interaction))
        await interaction.followup.send(
            f"Ladder **{ladder.name}** créé (`{ladder.id}`).", ephemeral=True
        )


class LadderSettingsButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:settings",
):
    """Open the ladder settings modal (admins only, guarded at click time)."""

    def __init__(self, ladder_id: str = "") -> None:
        self.ladder_id = ladder_id
        super().__init__(
            discord.ui.Button(
                label="Paramètres",
                style=discord.ButtonStyle.primary,
                custom_id=f"{_NS}:settings",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> LadderSettingsButton:
        """Rebuild the item from the wire (state read at click time)."""
        return cls(_client_ladder_id(interaction))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Handle the click: resolve, read the ladder, open the identity modal."""
        wiring = build_ladder_wiring()
        if wiring is None:
            await interaction.response.send_message("Ladder wiring indisponible.", ephemeral=True)
            return
        ladder_id = self.ladder_id or await _resolved_ladder_id(interaction, wiring)
        if not ladder_id:
            await interaction.response.send_message("Aucun ladder pour ce guild.", ephemeral=True)
            return
        ladder = await wiring.service.get_ladder(ladder_id)
        if ladder is None:
            await interaction.response.send_message("Ladder introuvable.", ephemeral=True)
            return
        s = ladder.settings
        await interaction.response.send_modal(
            LadderSettingsModal(ladder_id, name=ladder.name, rating=s.rating_system, ready=s.ready_timeout)
        )


class LadderSettingsModal(discord.ui.Modal):
    """The ladder identity form: name, rating method, ready timeout (<=5 fields)."""

    def __init__(self, ladder_id: str, name: str, rating: str, ready: int) -> None:
        self.ladder_id = ladder_id
        super().__init__(title="Paramètres du ladder", timeout=None)
        self.name: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label="Nom", default=name, max_length=100, required=True
        )
        self.rating: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label="Methode de ranking (elo / glicko2)", default=rating, max_length=16, required=True
        )
        self.ready: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label="Timeout ready (s)", default=str(ready), max_length=8
        )
        for item in (self.name, self.rating, self.ready):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Handle the form submission: validate, persist, confirm."""
        wiring = build_ladder_wiring()
        if wiring is None:
            await interaction.response.send_message("Ladder wiring indisponible.", ephemeral=True)
            return
        service = wiring.service
        ladder = await service.get_ladder(self.ladder_id)
        if ladder is None:
            await interaction.response.send_message("Ladder introuvable.", ephemeral=True)
            return
        try:
            updates: dict[str, Any] = {
                "name": str(self.name.value).strip() or ladder.name,
                "rating_system": str(self.rating.value).strip().lower() or ladder.settings.rating_system,
                "ready_timeout": int(self.ready.value or ladder.settings.ready_timeout),
            }
        except ValueError:
            await interaction.response.send_message(
                "Valeurs invalides : les nombres doivent etre entiers.", ephemeral=True
            )
            return
        merged = {**ladder.settings.model_dump(), **updates}
        settings_model = type(ladder.settings)
        updated = ladder.model_copy(update={"name": updates["name"], "settings": settings_model(**merged)})
        await service._db.upsert_entry(LADDERS_COLLECTION, updated.to_mongo())
        await service._audit_record("ladder.settings.update", {"ladder_id": self.ladder_id, "fields": sorted(updates)})
        await interaction.response.send_message(
            f"Paramètres enregistrés — `{updated.name}` (`{updated.settings.rating_system}`).",
            ephemeral=True,
        )




class LadderPickStrategySelect(
    discord.ui.DynamicItem[discord.ui.Select[Any]],
    template=rf"{_NS}:pick:set",
):
    """Switch the ladder's map-pick strategy (#223, admin-selectable)."""

    def __init__(self, options: list[discord.SelectOption] | None = None) -> None:
        super().__init__(
            discord.ui.Select(
                custom_id=f"{_NS}:pick:set",
                options=options or [discord.SelectOption(label="weighted", value="weighted")],
                placeholder="Mode de pick...",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> LadderPickStrategySelect:
        """Rebuild the select's options from the registry at click time."""
        from kingdoms.mods.ladder.pick_strategies import list_pick_strategies

        options = [
            discord.SelectOption(label=strategy.label, value=strategy.key)
            for strategy in list_pick_strategies()[:25]
        ]
        return cls(options)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Persist the chosen strategy into the ladder settings."""
        wiring = build_ladder_wiring()
        if wiring is None:
            await interaction.response.send_message("Ladder wiring indisponible.", ephemeral=True)
            return
        ladder_id = str(getattr(interaction.client, "_ladder_id", "") or "")
        ladder = await wiring.service.get_ladder(ladder_id)
        if ladder is None:
            await interaction.response.send_message("Ladder introuvable.", ephemeral=True)
            return
        from kingdoms.mods.ladder.pick_strategies import resolve_pick_strategy

        key = str(self.item.values[0])
        strategy = resolve_pick_strategy(key)
        merged = {**ladder.settings.model_dump(), "pick_strategy": strategy.key}
        settings_model = type(ladder.settings)
        updated = ladder.model_copy(update={"settings": settings_model(**merged)})
        await wiring.service._db.upsert_entry(LADDERS_COLLECTION, updated.to_mongo())
        await wiring.service._audit_record(
            "ladder.pick_strategy.update", {"ladder_id": ladder_id, "strategy": strategy.key}
        )
        await interaction.response.send_message(
            f"Mode de pick : **{strategy.label}** (`{strategy.key}`).", ephemeral=True
        )



class LadderEnrollButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:enroll:(?P<state>open|close)",
):
    """Open or close the enrollments (toggle, state rides the custom_id)."""

    def __init__(self, state: str) -> None:
        opening = state == "open"
        super().__init__(
            discord.ui.Button(
                label="Ouvrir les inscriptions" if opening else "Fermer les inscriptions",
                style=discord.ButtonStyle.success if opening else discord.ButtonStyle.secondary,
                custom_id=f"{_NS}:enroll:{state}",
            )
        )
        self.state = state

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> LadderEnrollButton:
        """Rebuild the item from the wire at click time."""
        del item
        return cls(match.group("state"))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Toggle the enrollments, audit, re-render the entry view."""
        wiring = build_ladder_wiring()
        if wiring is None:
            await interaction.response.send_message("Ladder wiring indisponible.", ephemeral=True)
            return
        ladder_id = await _resolved_ladder_id(interaction, wiring)
        if not ladder_id:
            await interaction.response.send_message("Aucun ladder pour ce guild.", ephemeral=True)
            return
        ladder = await wiring.service.get_ladder(ladder_id)
        current_open = bool(ladder.enrollments_open) if ladder is not None else True
        opening = self.state == "open"
        if opening == current_open:
            await interaction.response.edit_message(view=await ladder_admin_entry(interaction))
            return
        await wiring.service.set_enrollments_open(ladder_id, opening)
        await wiring.service._audit_record(
            "ladder.enrollments", {"ladder_id": ladder_id, "open": opening}
        )
        await interaction.response.edit_message(view=await ladder_admin_entry(interaction))
        await interaction.followup.send(
            "Inscriptions **ouvertes**." if opening else "Inscriptions **fermées**.",
            ephemeral=True,
        )


class LadderPauseButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:pause:(?P<state>pause|resume)",
):
    """Pause or resume the ladder queue (interrupt / start the ladder)."""

    def __init__(self, state: str) -> None:
        pausing = state == "pause"
        super().__init__(
            discord.ui.Button(
                label="Interrompre le ladder" if pausing else "Demarrer le ladder",
                style=discord.ButtonStyle.danger if pausing else discord.ButtonStyle.success,
                custom_id=f"{_NS}:pause:{state}",
            )
        )
        self.state = state

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> LadderPauseButton:
        """Rebuild the item from the wire at click time."""
        del item
        return cls(match.group("state"))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Toggle the queue pause, audit, re-render the entry view."""
        wiring = build_ladder_wiring()
        if wiring is None:
            await interaction.response.send_message("Ladder wiring indisponible.", ephemeral=True)
            return
        ladder_id = await _resolved_ladder_id(interaction, wiring)
        if not ladder_id:
            await interaction.response.send_message("Aucun ladder pour ce guild.", ephemeral=True)
            return
        ladder = await wiring.service.get_ladder(ladder_id)
        current_paused = bool(ladder.queue_paused) if ladder is not None else False
        pausing = self.state == "pause"
        if pausing == current_paused:
            await interaction.response.edit_message(view=await ladder_admin_entry(interaction))
            return
        if not pausing and ladder is not None:
            players = await wiring.service._db.find_ladder_players(ladder_id)
            pools = await wiring.game_data.list_map_pools(getattr(ladder, "game_key", GAME_KEY) or GAME_KEY)
            pool_ok = any(p.id == ladder.active_map_pool_id for p in pools)
            active_season = (
                await wiring.season_service.get_active_season(ladder_id) if wiring.season_service else None
            )
            if len(players) < 2 or not pool_ok or active_season is None:
                await interaction.response.edit_message(view=await ladder_admin_entry(interaction))
                await interaction.followup.send(
                    "Demarrage bloque : il faut au moins 2 inscrits, un map pool actif "
                    "et une saison en cours.",
                    ephemeral=True,
                )
                return
        await wiring.service.set_queue_paused(ladder_id, pausing)
        await wiring.service._audit_record(
            "ladder.queue", {"ladder_id": ladder_id, "paused": pausing}
        )
        await interaction.response.edit_message(view=await ladder_admin_entry(interaction))
        await interaction.followup.send(
            "Ladder **interrompu** (file en pause)." if pausing else "Ladder **démarré**.",
            ephemeral=True,
        )


class LadderSeasonButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:seasons",
):
    """Open the seasons sub-view (list + create/activate)."""

    def __init__(self, ladder_id: str = "") -> None:
        self.ladder_id = ladder_id
        super().__init__(
            discord.ui.Button(
                label="Saisons",
                style=discord.ButtonStyle.primary,
                custom_id=f"{_NS}:seasons",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> LadderSeasonButton:
        """Rebuild the item from the wire (state read at click time)."""
        return cls(_client_ladder_id(interaction))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Handle the click: resolve the ladder, show the seasons view."""
        wiring = build_ladder_wiring()
        ladder_id = self.ladder_id
        if wiring is not None and not ladder_id:
            ladder_id = await _resolved_ladder_id(interaction, wiring)
        if not ladder_id:
            await interaction.response.send_message("Aucun ladder pour ce guild.", ephemeral=True)
            return
        await interaction.response.edit_message(view=await seasons_view(ladder_id))


async def _provision_season_surface(interaction: discord.Interaction, season: Any) -> None:
    """Provision a season's roles and salons immediately (best-effort)."""
    import logging as _logging

    from kingdoms.core.services.season_roles import PLAYER_ROLE_KEY, STAFF_ROLE_KEY

    logger = _logging.getLogger("kingdoms.ladder.admin_panel")
    guild = getattr(interaction, "guild", None)
    guild_id = str(getattr(interaction, "guild_id", "") or "")
    if guild is None or not guild_id:
        return
    season_roles = getattr(interaction.client, "season_roles_service", None)
    if season_roles is not None:
        for kind in (PLAYER_ROLE_KEY, STAFF_ROLE_KEY):
            try:
                await season_roles.ensure_role(guild_id, kind, season.name)
            except Exception:
                logger.warning("season role provisioning failed (%s)", kind, exc_info=True)
    try:
        from kingdoms.mods.ladder.channels import sync_ladder_channels

        await sync_ladder_channels(guild, interaction.client)
    except Exception:
        logger.warning("season channel provisioning failed", exc_info=True)


class LadderSeasonCreateButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:seasons:create",
):
    """Open the season-creation modal."""

    def __init__(self, ladder_id: str = "") -> None:
        self.ladder_id = ladder_id
        super().__init__(
            discord.ui.Button(
                label="Créer une saison",
                style=discord.ButtonStyle.success,
                custom_id=f"{_NS}:seasons:create",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> LadderSeasonCreateButton:
        """Rebuild the item from the wire (state read at click time)."""
        return cls(_client_ladder_id(interaction))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Handle the click: resolve the ladder, open the modal."""
        wiring = build_ladder_wiring()
        ladder_id = self.ladder_id
        if wiring is not None and not ladder_id:
            ladder_id = await _resolved_ladder_id(interaction, wiring)
        if not ladder_id:
            await interaction.response.send_message("Aucun ladder pour ce guild.", ephemeral=True)
            return
        from kingdoms.mods.ladder.season_wizard import start_season_wizard

        await start_season_wizard(interaction, ladder_id)


class LadderSeasonActivateSelect(
    discord.ui.DynamicItem[discord.ui.Select[Any]],
    template=rf"{_NS}:seasons:activate",
):
    """Activate a season of the ladder (transactional pool switch)."""

    def __init__(self, options: list[discord.SelectOption] | None = None) -> None:
        options = options or []
        super().__init__(
            discord.ui.Select(
                custom_id=f"{_NS}:seasons:activate",
                options=options or [discord.SelectOption(label="Vide", value="none")],
                placeholder="Activer une saison...",
                disabled=not options,
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> LadderSeasonActivateSelect:
        """Rebuild the select's options from the wire at click time."""
        wiring = build_ladder_wiring()
        options: list[discord.SelectOption] = []
        if wiring is not None and wiring.season_service is not None:
            ladder = await _resolve_ladder(interaction, wiring)
            if ladder is not None:
                seasons = await wiring.season_service.list_seasons(str(ladder["_id"]))
                options = [discord.SelectOption(label=f"{s.name} ({s.state})", value=s.id) for s in seasons[-25:]]
        return cls(options)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Handle the selection: activate the chosen season."""
        wiring = build_ladder_wiring()
        if wiring is None or wiring.season_service is None:
            await interaction.response.send_message("Ladder wiring indisponible.", ephemeral=True)
            return
        chosen = (_selected_values(interaction) or [""])[0]
        if not chosen or chosen == "none":
            await interaction.response.defer()
            return
        try:
            season = await wiring.season_service.activate_season(chosen, _now_ms())
        except Exception:
            logger.exception("LADDER ADMIN: season activation failed")
            await interaction.response.send_message("Activation echouee (voir les logs).", ephemeral=True)
            return
        await _provision_season_surface(interaction, season)
        await interaction.response.send_message(f"Saison **{season.name}** activee.", ephemeral=True)


class LadderPoolButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:pool",
):
    """Open the map-pool sub-view (list + switch)."""

    def __init__(self, ladder_id: str = "") -> None:
        self.ladder_id = ladder_id
        super().__init__(
            discord.ui.Button(
                label="Map pool",
                style=discord.ButtonStyle.primary,
                custom_id=f"{_NS}:pool",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> LadderPoolButton:
        """Rebuild the item from the wire (state read at click time)."""
        return cls(_client_ladder_id(interaction))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Handle the click: show the map-pool view."""
        wiring = build_ladder_wiring()
        ladder_id = self.ladder_id
        if wiring is not None and not ladder_id:
            ladder_id = await _resolved_ladder_id(interaction, wiring)
        if not ladder_id:
            await interaction.response.send_message("Aucun ladder pour ce guild.", ephemeral=True)
            return
        await interaction.response.edit_message(view=await pools_view(ladder_id))


class LadderPoolSelect(
    discord.ui.DynamicItem[discord.ui.Select[Any]],
    template=rf"{_NS}:pool:set",
):
    """Switch the ladder's active map pool."""

    def __init__(self, options: list[discord.SelectOption] | None = None) -> None:
        super().__init__(
            discord.ui.Select(
                custom_id=f"{_NS}:pool:set",
                options=options or [discord.SelectOption(label="Aucun pool", value="none")],
                placeholder="Pool actif...",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> LadderPoolSelect:
        """Rebuild the select's options from the wire at click time."""
        wiring = build_ladder_wiring()
        options: list[discord.SelectOption] = []
        if wiring is not None:
            pools = await wiring.game_data.list_map_pools(GAME_KEY)
            options = [discord.SelectOption(label=p.name, value=p.id) for p in pools[:25]]
        return cls(options)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Handle the selection: switch the active pool."""
        wiring = build_ladder_wiring()
        if wiring is None:
            await interaction.response.send_message("Ladder wiring indisponible.", ephemeral=True)
            return
        chosen = (_selected_values(interaction) or [""])[0]
        if not chosen or chosen == "none":
            await interaction.response.defer()
            return
        ladder = await _resolve_ladder(interaction, wiring)
        if ladder is None:
            await interaction.response.send_message("Aucun ladder pour ce guild.", ephemeral=True)
            return
        try:
            await wiring.service.set_active_pool(str(ladder["_id"]), chosen)
        except Exception:
            logger.exception("LADDER ADMIN: pool switch failed")
            await interaction.response.send_message("Switch echoue (voir les logs).", ephemeral=True)
            return
        pool = await wiring.game_data.get_map_pool(chosen)
        await interaction.response.send_message(
            f"Pool actif : **{pool.name if pool else chosen}**.",
            ephemeral=True,
        )


async def seasons_view(ladder_id: str) -> discord.ui.LayoutView:
    """Build the seasons sub-view: list, create, activate - back included."""
    wiring = build_ladder_wiring()
    view = discord.ui.LayoutView(timeout=None)
    blocks: list[Any] = [discord.ui.TextDisplay("# Saisons du ladder")]
    if wiring is None or wiring.season_service is None:
        blocks.append(discord.ui.TextDisplay("Ladder wiring indisponible."))
        view.add_item(discord.ui.Container(*blocks))
        view.add_item(_back_row())
        return view
    seasons = await wiring.season_service.list_seasons(ladder_id)
    active = await wiring.season_service.get_active_season(ladder_id)
    if not seasons:
        blocks.append(discord.ui.TextDisplay("_Aucune saison - crée-la (bouton « Créer une saison »)._"))
    else:
        lines = []
        for s in seasons[-10:]:
            marker = "active" if s.id == (active.id if active else "") else s.state
            lines.append(f"- {s.name} ({marker}) - pool {s.map_pool_id or 'aucun'}")
        blocks.append(discord.ui.TextDisplay("\n".join(lines)))
    view.add_item(discord.ui.Container(*blocks))
    if seasons:
        select_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
        select_row.add_item(
            LadderSeasonActivateSelect(
                [discord.SelectOption(label=f"{s.name} ({s.state})", value=s.id) for s in seasons[-25:]]
            )
        )
        view.add_item(select_row)
    action_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    action_row.add_item(LadderSeasonCreateButton(ladder_id))
    view.add_item(action_row)
    view.add_item(_back_row())
    return view


async def pools_view(ladder_id: str) -> discord.ui.LayoutView:
    """Build the map-pool sub-view: switch the active pool - back included."""
    wiring = build_ladder_wiring()
    view = discord.ui.LayoutView(timeout=None)
    blocks: list[Any] = [discord.ui.TextDisplay("# Map pool du ladder")]
    if wiring is None:
        blocks.append(discord.ui.TextDisplay("Ladder wiring indisponible."))
        view.add_item(discord.ui.Container(*blocks))
        view.add_item(_back_row())
        return view
    pools = await wiring.game_data.list_map_pools(GAME_KEY)
    active_id = None
    ladder = await wiring.service.get_ladder(ladder_id)
    if ladder is not None:
        active_id = ladder.active_map_pool_id
    if not pools:
        blocks.append(discord.ui.TextDisplay("_Aucun pool - seed les donnees de jeu._"))
    else:
        lines = [f"- {p.name} ({p.id})" + (" [actif]" if p.id == active_id else "") for p in pools[:15]]
        blocks.append(discord.ui.TextDisplay("\n".join(lines)))
    view.add_item(discord.ui.Container(*blocks))
    select_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    select_row.add_item(LadderPoolSelect([discord.SelectOption(label=p.name, value=p.id) for p in pools[:25]]))
    view.add_item(select_row)
    view.add_item(_back_row())
    return view




async def ladder_mod_admin_view(
    interaction: discord.Interaction | ModAdminPinInteraction,
) -> discord.ui.LayoutView:
    """Render the mod-level admin view: the seasons' lifecycle (cross-season).

    The root \U0001f6e1-ladder-admin channel hosts this pinned panel: it
    creates seasons, activates one (the active season is defined here),
    ends it, and toggles the enrollments. The per-season configuration
    lives in the season's own admin salon; this panel owns the cycle.
    """
    wiring = build_ladder_wiring()
    view = discord.ui.LayoutView(timeout=None)
    blocks: list[Any] = [
        discord.ui.TextDisplay(
            f"# \U0001f6e1 {_t(_catalog(interaction), await _locale(interaction), 'admin_title_seasons')}"
        )
    ]
    if wiring is None or wiring.season_service is None:
        blocks.append(discord.ui.TextDisplay("Ladder wiring indisponible : Mongo/Redis ne sont pas configur\u00e9s."))
        view.add_item(discord.ui.Container(*blocks, accent_colour=discord.Colour(0x5865F2)))
        return view
    ladder = await _resolve_ladder(interaction, wiring)
    if ladder is None:
        blocks.append(discord.ui.TextDisplay("Aucun ladder \u2014 le noyau le cr\u00e9era au premier usage."))
        view.add_item(discord.ui.Container(*blocks, accent_colour=discord.Colour(0x5865F2)))
        return view
    ladder_id = str(ladder["_id"])
    seasons = await wiring.season_service.list_seasons(ladder_id)
    active = await wiring.season_service.get_active_season(ladder_id)
    enrollments_open = bool(ladder.get("enrollments_open", True))
    catalog = _catalog(interaction)
    locale = await _locale(interaction)
    active_label = _t(catalog, locale, "season_active")
    none_label = _t(catalog, locale, "season_none")
    lines = [f"**{active_label}** : {active.name if active else '_' + none_label + '_'}"]
    recent = seasons[-5:]
    if recent:
        active_id = active.id if active else ""
        parts = []
        for s in recent:
            state_key = "season_state.active" if s.id == active_id else f"season_state.{s.state}"
            parts.append(f"\n- {s.name} ({_t(catalog, locale, state_key)})")
        lines.append("".join(parts))
    else:
        lines.append("\n_" + _t(catalog, locale, "season_empty") + "_")
    lines.append(
        f"\nInscriptions : **{_t(catalog, locale, 'enrollments_open' if enrollments_open else 'enrollments_closed')}**"
        + " \u2014 "
        + _t(catalog, locale, "season_list_hint")
    )
    blocks.append(discord.ui.TextDisplay("\n".join(lines)))
    view.add_item(discord.ui.Container(*blocks, accent_colour=discord.Colour(0x5865F2)))
    lifecycle: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    lifecycle.add_item(LadderSeasonCreateButton(ladder_id))
    if seasons:
        lifecycle.add_item(LadderSeasonEndButton())
    view.add_item(lifecycle)
    if seasons:
        select_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
        select_row.add_item(
            LadderSeasonActivateSelect(
                [discord.SelectOption(label=f"{s.name} ({s.state})", value=s.id) for s in seasons[-25:]]
            )
        )
        view.add_item(select_row)
    cycle: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    cycle.add_item(LadderEnrollButton("open" if not enrollments_open else "close"))
    view.add_item(cycle)
    return view


class LadderSeasonEndButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:seasons:end",
):
    """End the active season (the season admin salon follows at the next sync)."""

    def __init__(self) -> None:
        super().__init__(
            discord.ui.Button(
                label="Arreter la saison",
                style=discord.ButtonStyle.danger,
                custom_id=f"{_NS}:seasons:end",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> LadderSeasonEndButton:
        """Rebuild the item from the wire."""
        del interaction, item, match
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        """End the active season, audit, re-render the lifecycle view."""
        wiring = build_ladder_wiring()
        if wiring is None or wiring.season_service is None:
            await interaction.response.send_message("Ladder wiring indisponible.", ephemeral=True)
            return
        ladder_id = await _resolved_ladder_id(interaction, wiring)
        if not ladder_id:
            await interaction.response.send_message("Aucun ladder pour ce guild.", ephemeral=True)
            return
        active = await wiring.season_service.get_active_season(ladder_id)
        if active is None:
            await interaction.response.send_message("Aucune saison active.", ephemeral=True)
            return
        ended = await wiring.season_service.end_season(active.id, _now_ms())
        await wiring.service._audit_record("season.end", {"season_id": ended.id, "ladder_id": ladder_id})
        await interaction.response.edit_message(view=await ladder_mod_admin_view(interaction))
        await interaction.followup.send(f"Saison **{ended.name}** arretee.", ephemeral=True)


def register_ladder_admin_section() -> None:
    """Register the ladder section into the /admin panel (idempotent)."""
    register_admin_mod_section(
        AdminModSection(
            mod=MOD_KEY,
            label="Ladder",
            description="Ladder settings, ratings and matches",
            entry=ladder_admin_entry,
        )
    )


def register_ladder_admin_items(bot: discord.Client) -> None:
    """Register the section's DynamicItems (called at every startup)."""
    bot.add_dynamic_items(
        LadderCreateButton,
        LadderSeasonEndButton,
        LadderSettingsButton,
        LadderSeasonButton,
        LadderSeasonCreateButton,
        LadderSeasonActivateSelect,
        LadderPoolButton,
        LadderPoolSelect,
        LadderEnrollButton,
        LadderPauseButton,
        LadderPickStrategySelect,
    )
