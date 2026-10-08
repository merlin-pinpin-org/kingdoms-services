"""The guided season-creation wizard: one question at a time (#932b32fd).

Creating a season well needs answers the admin should not have to dig
for: name, map pool, pick mode, ranking system, fav/ban quotas, and
the enrollment policy. This wizard asks them **in sequence**, each
step on its own ephemeral view, and explains as it goes — notably
that enrollments may stay open during the season (it is a ladder,
not a tournament). The final step shows a recap and one button that
creates the season, applies the settings, and opens enrollments.

The state lives in the ephemeral interaction chain (each step's
callback rebuilds the view with the accumulated answers); no
server-side session store is needed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import discord

logger = logging.getLogger("kingdoms.ladder.season_wizard")

_WIZARD_NS = "ladder:wizard"


async def _ladder_game_key(state: WizardState) -> str:
    """Resolve the ladder's game key; empty when unavailable."""
    wiring = _wiring()
    if wiring is None or wiring.service is None:
        return ""
    try:
        ladder = await wiring.service.get_ladder(state.ladder_id)
    except Exception:
        logger.exception("SEASON WIZARD: ladder lookup failed")
        return ""
    return getattr(ladder, "game_key", "") or ""


@dataclass
class WizardState:
    """The accumulated answers of one wizard run."""

    ladder_id: str
    name: str = ""
    pool_id: str = ""
    pool_name: str = ""
    pick_strategy: str = ""
    pick_label: str = ""
    rating_system: str = ""
    fav_count: int | None = None
    ban_count: int | None = None
    random_ban_count: int | None = None
    days: int | None = None



def _wiring() -> Any | None:
    from kingdoms.mods.ladder.commands import build_ladder_wiring

    return build_ladder_wiring()


async def start_season_wizard(interaction: discord.Interaction, ladder_id: str) -> None:
    """Open the wizard's first step: the season name."""
    state = WizardState(ladder_id=ladder_id)
    await interaction.response.send_modal(SeasonNameModal(state))


class SeasonNameModal(discord.ui.Modal):
    """Step 1: the season name."""

    def __init__(self, state: WizardState) -> None:
        self.state = state
        super().__init__(title="Saison — 1/6 : nom", timeout=600)
        self.name: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label="Nom de la saison (ex. s2)", max_length=32, required=True
        )
        self.add_item(self.name)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Record the name; move on to the pool step."""
        self.state.name = str(self.name.value).strip() or "s?"
        await _step_pool(interaction, self.state)


async def _step_pool(interaction: discord.Interaction, state: WizardState) -> None:
    """Step 2: pick the season's map pool (from the guild's visible pools)."""
    wiring = _wiring()
    game_key = await _ladder_game_key(state)
    pools: list[Any] = []
    if wiring is not None and game_key:
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        pools = await wiring.game_data.list_map_pools(game_key, guild_id=guild_id or None)
    options = [discord.SelectOption(label=p.name, value=p.id) for p in pools[:24]]
    if options:
        select_options = options
    else:
        empty_label = f"Aucun pool — à créer dans games/{game_key or '?'}-map-pools"
        select_options = [discord.SelectOption(label=empty_label, value="none")]
    view = discord.ui.View(timeout=600)
    select: discord.ui.Select[Any] = discord.ui.Select(
        placeholder="Map pool de la saison...",
        options=select_options,
    )

    async def _pick(inner: discord.Interaction) -> None:
        chosen = (getattr(inner, "values", None) or [""])[0]
        if chosen != "none":
            state.pool_id = chosen
            state.pool_name = next((p.name for p in pools if p.id == chosen), chosen)
        await _step_pick_mode(inner, state)

    select.callback = _pick  # type: ignore[method-assign, assignment]
    view.add_item(select)
    pools_forum = f"games/{game_key or '?'}-map-pools"
    content = f"Saison **{state.name}** — 2/6 : le map pool\nLes pools se gèrent dans `{pools_forum}` (forum)."
    if interaction.response.is_done():
        await interaction.followup.send(content=content, view=view, ephemeral=True)
    else:
        await interaction.response.send_message(content=content, view=view, ephemeral=True)


async def _step_pick_mode(interaction: discord.Interaction, state: WizardState) -> None:
    """Step 3: the map-pick mode (how favs/bans choose the map)."""
    from kingdoms.mods.ladder.pick_strategies import list_pick_strategies

    strategies = list_pick_strategies()
    options = [discord.SelectOption(label=s.label, value=s.key) for s in strategies[:24]]
    view = discord.ui.View(timeout=600)
    select: discord.ui.Select[Any] = discord.ui.Select(placeholder="Mode de sélection des maps...", options=options)

    async def _pick(inner: discord.Interaction) -> None:
        chosen = (getattr(inner, "values", None) or [""])[0]
        state.pick_strategy = chosen
        state.pick_label = next((s.label for s in strategies if s.key == chosen), chosen)
        await _step_rating(inner, state)

    select.callback = _pick  # type: ignore[method-assign, assignment]
    view.add_item(select)
    content = (
        f"Saison **{state.name}** — 3/6 : le mode de pick\n"
        "Comment la map d'une partie est choisie à partir des favoris/bans des joueurs."
    )
    if interaction.response.is_done():
        await interaction.followup.send(content=content, view=view, ephemeral=True)
    else:
        await interaction.response.send_message(content=content, view=view, ephemeral=True)


async def _step_rating(interaction: discord.Interaction, state: WizardState) -> None:
    """Step 4: the ranking system (elo / glicko2)."""
    view = discord.ui.View(timeout=600)
    select: discord.ui.Select[Any] = discord.ui.Select(
        placeholder="Type de ranking...",
        options=[
            discord.SelectOption(label="Elo", value="elo", description="Classique, simple à expliquer"),
            discord.SelectOption(label="Glicko2", value="glicko2", description="Plus précis, gère l'incertitude (RD)"),
        ],
    )

    async def _pick(inner: discord.Interaction) -> None:
        state.rating_system = (getattr(inner, "values", None) or ["elo"])[0]
        await inner.response.send_modal(QuotasModal(state))

    select.callback = _pick  # type: ignore[method-assign, assignment]
    view.add_item(select)
    content = (
        f"Saison **{state.name}** — 4/6 : le type de ranking\n"
        f"Les nouveaux joueurs commencent avec le rating initial du système choisi."
    )
    if interaction.response.is_done():
        await interaction.followup.send(content=content, view=view, ephemeral=True)
    else:
        await interaction.response.send_message(content=content, view=view, ephemeral=True)


class QuotasModal(discord.ui.Modal):
    """Step 5: fav/ban/random-ban counts."""

    def __init__(self, state: WizardState) -> None:
        self.state = state
        super().__init__(title="Saison — 5/6 : favoris / bans", timeout=600)
        self.favs: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label="Nombre de maps favorites par joueur", placeholder="3", max_length=2, required=True
        )
        self.bans: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label="Nombre de bans par joueur", placeholder="2", max_length=2, required=True
        )
        self.random_bans: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label="Nombre de bans aléatoires (hors prefs)", placeholder="2", max_length=2, required=True
        )
        self.add_item(self.favs)
        self.add_item(self.bans)
        self.add_item(self.random_bans)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Record the quotas; move on to the duration step."""
        try:
            self.state.fav_count = int(str(self.favs.value).strip())
            self.state.ban_count = int(str(self.bans.value).strip())
            self.state.random_ban_count = int(str(self.random_bans.value).strip())
        except ValueError:
            await interaction.response.send_message(
                "Comptes invalides : donne des nombres entiers.", ephemeral=True
            )
            return
        await _step_duration(interaction, self.state)


async def _step_duration(interaction: discord.Interaction, state: WizardState) -> None:
    """Step 6: optional duration, then the recap."""
    await interaction.response.send_modal(DurationModal(state))


class DurationModal(discord.ui.Modal):
    """Step 6: the optional season duration (days)."""

    def __init__(self, state: WizardState) -> None:
        self.state = state
        super().__init__(title="Saison — 6/6 : durée", timeout=600)
        self.days: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label="Durée en jours (vide = pas de fin programmée)", placeholder="90", max_length=4, required=False
        )
        self.add_item(self.days)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Record the optional duration; move on to the recap."""
        raw = str(self.days.value or "").strip()
        if raw:
            try:
                self.state.days = int(raw)
            except ValueError:
                await interaction.response.send_message("Durée invalide (un nombre de jours).", ephemeral=True)
                return
        await _step_recap(interaction, self.state)


async def _step_recap(interaction: discord.Interaction, state: WizardState) -> None:
    """Show the recap; one button creates the season and opens enrollments."""
    lines = [
        f"## Récapitulatif de la saison **{state.name}**",
        f"- Map pool : **{state.pool_name or '— aucun —'}**",
        f"- Mode de pick : **{state.pick_label or state.pick_strategy or '—'}**",
        f"- Ranking : **{state.rating_system or 'elo'}**",
        f"- Favoris : **{state.fav_count}** / Bans : **{state.ban_count}**"
        f" / Bans aléatoires : **{state.random_ban_count}**",
        f"- Durée : **{state.days} jours**" if state.days else "- Durée : **sans fin programmée**",
        "",
        "Les inscriptions seront **ouvertes** à la création — et peuvent le rester :",
        "c'est un ladder, les joueurs peuvent rejoindre en cours de saison.",
    ]
    view = discord.ui.View(timeout=600)
    button: discord.ui.Button[Any] = discord.ui.Button(
        label="Créer la saison et ouvrir les inscriptions", style=discord.ButtonStyle.success
    )

    async def _create(inner: discord.Interaction) -> None:
        await _finish(inner, state)

    button.callback = _create  # type: ignore[method-assign, assignment]
    view.add_item(button)
    if interaction.response.is_done():
        await interaction.followup.send(content="\n".join(lines), view=view, ephemeral=True)
    else:
        await interaction.response.send_message(content="\n".join(lines), view=view, ephemeral=True)


async def _finish(interaction: discord.Interaction, state: WizardState) -> None:
    """Create the season, apply the settings, open the enrollments."""
    from kingdoms.mods.ladder.admin_panel import _provision_season_surface

    wiring = _wiring()
    if wiring is None or wiring.season_service is None:
        await interaction.response.edit_message(content="Ladder wiring indisponible.")
        return
    now = int(discord.utils.utcnow().timestamp() * 1000)
    try:
        season = await wiring.season_service.create_season(
            state.ladder_id,
            state.name,
            state.pool_id or None,
            start_at=now,
            end_at=now + state.days * 86_400_000 if state.days else None,
        )
    except Exception:
        logger.exception("SEASON WIZARD: creation failed")
        await interaction.response.edit_message(content="Création échouée (nom déjà pris ?).")
        return
    await _apply_wizard_settings(interaction, state, wiring)
    try:
        await wiring.service.set_enrollments_open(state.ladder_id, True)
    except Exception:
        logger.exception("SEASON WIZARD: enrollments open failed")
    try:
        await _provision_season_surface(interaction, season)
    except Exception:
        logger.exception("SEASON WIZARD: season surface provisioning failed")
    await interaction.response.edit_message(
        content=(
            f"Saison **{season.name}** créée, réglages appliqués, inscriptions **ouvertes**.\n"
            "Elles peuvent rester ouvertes pendant toute la saison — active la saison "
            "depuis le menu ladder admin quand tu veux lancer."
        ),
        view=None,
    )


async def _apply_wizard_settings(
    interaction: discord.Interaction, state: WizardState, wiring: Any
) -> None:
    """Best-effort apply of the wizard's answers onto the ladder settings."""
    changes: dict[str, Any] = {}
    if state.pick_strategy:
        changes["pick_strategy"] = state.pick_strategy
    if state.rating_system:
        changes["rating_system"] = state.rating_system
    for key, value in (
        ("player_fav_count", state.fav_count),
        ("player_ban_count", state.ban_count),
        ("random_ban_count", state.random_ban_count),
    ):
        if value is not None:
            changes[key] = value
    if not changes:
        return
    try:
        await wiring.service.admin.update_settings(state.ladder_id, str(interaction.user.id), changes)
    except Exception:
        logger.exception("SEASON WIZARD: settings apply failed")
