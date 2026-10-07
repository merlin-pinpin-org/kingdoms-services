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
from kingdoms.discord.ladder_commands import build_ladder_wiring

logger = logging.getLogger("kingdoms.ladder.admin_panel")

MOD_KEY = "ladder"
_NS = "admin:pin:modladder"
GAME_KEY = "aoe2"
LADDERS_COLLECTION = "ladders"


def _now_ms() -> int:
    """Epoch milliseconds, the repo's timestamp convention."""
    import time

    return int(time.time() * 1000)


async def _resolve_ladder(interaction: discord.Interaction, wiring: Any) -> dict[str, Any] | None:
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


async def ladder_admin_entry(interaction: discord.Interaction) -> discord.ui.LayoutView:
    """Render the ladder admin section's view (snapshot + config actions)."""
    wiring = build_ladder_wiring()
    view = discord.ui.LayoutView(timeout=None)
    blocks: list[Any] = [discord.ui.TextDisplay("# Ladder admin")]
    if wiring is None:
        blocks.append(discord.ui.TextDisplay("Ladder wiring indisponible : Mongo/Redis ne sont pas configurés."))
        view.add_item(discord.ui.Container(*blocks, accent_colour=discord.Colour(0x5865F2)))
        view.add_item(_back_row())
        return view

    ladder = await _resolve_ladder(interaction, wiring)
    if ladder is None:
        blocks.append(discord.ui.TextDisplay("Aucun ladder pour ce guild - cree-le ici."))
        view.add_item(discord.ui.Container(*blocks, accent_colour=discord.Colour(0x5865F2)))
        create_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
        create_row.add_item(LadderCreateButton())
        view.add_item(create_row)
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
                f"Inscriptions : **{'ouvertes' if ladder.get('enrollments_open', True) else 'fermees'}** - "
                f"File : **{'en pause' if ladder.get('queue_paused', False) else 'active'}**"
            ),
            discord.ui.TextDisplay(
                f"**Pool actif** : {active_pool.name if active_pool else '-'}\n"
                + ("\n".join(season_lines) if season_lines else "_Aucune saison_")
            ),
        ]
    )
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
    cycle.add_item(
        LadderPauseButton("pause" if not ladder.get("queue_paused", False) else "resume")
    )
    view.add_item(discord.ui.Container(*blocks, accent_colour=discord.Colour(0x5865F2)))
    view.add_item(actions)
    view.add_item(pick_row)
    view.add_item(cycle)
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
        if owner_ref.startswith("ladder:aoe2:"):
            owner_ref = owner_ref[len("ladder:aoe2:"):]
        if not owner_ref:
            guild_id = str(interaction.guild_id) if interaction.guild_id else ""
            owner_ref = f"guild:{guild_id}" if guild_id else "default"
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
            f"Ladder **{ladder.name}** cree (`{ladder.id}`).", ephemeral=True
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
                label="Parametres",
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
        super().__init__(title="Parametres du ladder", timeout=None)
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
            f"Parametres enregistres - `{updated.name}` (`{updated.settings.rating_system}`).",
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
        await wiring.service.set_enrollments_open(ladder_id, self.state == "open")
        await wiring.service._audit_record(
            "ladder.enrollments", {"ladder_id": ladder_id, "open": self.state == "open"}
        )
        await interaction.response.edit_message(view=await ladder_admin_entry(interaction))
        await interaction.followup.send(
            "Inscriptions **ouvertes**." if self.state == "open" else "Inscriptions **fermees**.",
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
        await wiring.service.set_queue_paused(ladder_id, self.state == "pause")
        await wiring.service._audit_record(
            "ladder.queue", {"ladder_id": ladder_id, "paused": self.state == "pause"}
        )
        await interaction.response.edit_message(view=await ladder_admin_entry(interaction))
        await interaction.followup.send(
            "Ladder **interrompu** (file en pause)." if self.state == "pause" else "Ladder **demarre**.",
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


class LadderSeasonCreateButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:seasons:create",
):
    """Open the season-creation modal."""

    def __init__(self, ladder_id: str = "") -> None:
        self.ladder_id = ladder_id
        super().__init__(
            discord.ui.Button(
                label="Creer une saison",
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
        await interaction.response.send_modal(LadderSeasonModal(ladder_id))


class LadderSeasonModal(discord.ui.Modal):
    """The season-creation form: name, pool, window, rating reset."""

    def __init__(self, ladder_id: str) -> None:
        self.ladder_id = ladder_id
        super().__init__(title="Creer une saison", timeout=None)
        self.name: discord.ui.TextInput[Any] = discord.ui.TextInput(label="Nom (ex. s2)", max_length=32, required=True)
        self.pool: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label="Map pool id (optionnel - vide = pool actif)", max_length=100, required=False
        )
        self.days: discord.ui.TextInput[Any] = discord.ui.TextInput(label="Duree (jours)", default="90", max_length=5)
        self.reset: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label="Reset des ratings (oui/non)", default="non", max_length=5
        )
        self.add_item(self.name)
        self.add_item(self.pool)
        self.add_item(self.days)
        self.add_item(self.reset)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Handle the form submission: validate, create, confirm."""
        wiring = build_ladder_wiring()
        if wiring is None or wiring.season_service is None:
            await interaction.response.send_message("Ladder wiring indisponible.", ephemeral=True)
            return
        now = _now_ms()
        pool_value: str | None = None
        ladder = await wiring.service.get_ladder(self.ladder_id)
        if ladder is not None and ladder.active_map_pool_id:
            pool_value = str(ladder.active_map_pool_id)
        else:
            pools = await wiring.game_data.list_map_pools(GAME_KEY)
            pool_value = pools[0].id if pools else None
        days = 90
        if str(self.days.value or "").strip():
            try:
                days = int(str(self.days.value).strip())
            except ValueError:
                await interaction.response.send_message("Duree invalide.", ephemeral=True)
                return
        try:
            season = await wiring.season_service.create_season(
                self.ladder_id,
                str(self.name.value).strip(),
                pool_value,
                start_at=now,
                end_at=now + days * 86_400_000,
            )
        except Exception:
            logger.exception("LADDER ADMIN: season creation failed")
            await interaction.response.send_message("Creation echouee (voir les logs).", ephemeral=True)
            return
        await interaction.response.send_message(
            f"Saison **{season.name}** creee (`{season.id}`) - active-la depuis la liste.",
            ephemeral=True,
        )


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
        blocks.append(discord.ui.TextDisplay("_Aucune saison - cree la premiere._"))
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
