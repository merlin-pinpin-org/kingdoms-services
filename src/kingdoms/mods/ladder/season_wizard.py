"""The ladder's season-creation steps, on the core wizard engine.

The generic machinery (state, ephemeral plumbing, select/confirm
builders) lives in the core (``kingdoms.discord.season_wizard``); this
module owns the ladder's **steps**: name, map pool (resolved from the
ladder's game), pick mode, ranking system, fav/ban quotas, duration,
then a recap that creates the season, applies the settings and opens
enrollments.

The state lives in the ephemeral interaction chain; no server-side
session store is needed.
"""

from __future__ import annotations

import logging
from typing import Any

import discord

from kingdoms.discord.season_wizard import (
    WizardState,
    ask_confirm,
    ask_select,
    parse_int,
    send_step,
)

logger = logging.getLogger("kingdoms.ladder.season_wizard")


def _wiring() -> Any | None:
    from kingdoms.mods.ladder.commands import build_ladder_wiring

    return build_ladder_wiring()


async def _ladder_game_key(state: WizardState) -> str:
    """Resolve the ladder's game key; empty when unavailable."""
    wiring = _wiring()
    if wiring is None or wiring.service is None:
        return ""
    try:
        ladder = await wiring.service.get_ladder(state.subject_id)
    except Exception:
        logger.exception("SEASON WIZARD: ladder lookup failed")
        return ""
    return getattr(ladder, "game_key", "") or ""


async def start_season_wizard(interaction: discord.Interaction, ladder_id: str) -> None:
    """Open the wizard's first step: the season name."""
    state = WizardState(subject_id=ladder_id)
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

    async def on_pick(inner: discord.Interaction, chosen: str, label: str) -> None:
        if chosen != "none":
            state.set("pool_id", chosen)
            state.set("pool_name", label)
        await _step_pick_mode(inner, state)

    pools_forum = f"games/{game_key or '?'}-map-pools"
    await ask_select(
        interaction,
        placeholder="Map pool de la saison...",
        options=options,
        content=(
            f"Saison **{state.name}** — 2/6 : le map pool\n"
            f"Les pools se gèrent dans `{pools_forum}` (forum)."
        ),
        on_pick=on_pick,
        empty_label=f"Aucun pool — à créer dans {pools_forum}",
    )


async def _step_pick_mode(interaction: discord.Interaction, state: WizardState) -> None:
    """Step 3: the map-pick mode (how favs/bans choose the map)."""
    from kingdoms.mods.ladder.pick_strategies import list_pick_strategies

    strategies = list_pick_strategies()
    options = [discord.SelectOption(label=s.label, value=s.key) for s in strategies[:24]]

    async def on_pick(inner: discord.Interaction, chosen: str, label: str) -> None:
        state.set("pick_strategy", chosen)
        state.set("pick_label", label)
        await _step_rating(inner, state)

    await ask_select(
        interaction,
        placeholder="Mode de sélection des maps...",
        options=options,
        content=(
            f"Saison **{state.name}** — 3/6 : le mode de pick\n"
            "Comment la map d'une partie est choisie à partir des favoris/bans des joueurs."
        ),
        on_pick=on_pick,
    )


async def _step_rating(interaction: discord.Interaction, state: WizardState) -> None:
    """Step 4: the ranking system (elo / glicko2)."""
    options = [
        discord.SelectOption(label="Elo", value="elo", description="Classique, simple à expliquer"),
        discord.SelectOption(
            label="Glicko2", value="glicko2", description="Plus précis, gère l'incertitude (RD)"
        ),
    ]

    async def on_pick(inner: discord.Interaction, chosen: str, label: str) -> None:
        state.set("rating_system", chosen or "elo")
        await inner.response.send_modal(QuotasModal(state))

    await ask_select(
        interaction,
        placeholder="Type de ranking...",
        options=options,
        content=(
            f"Saison **{state.name}** — 4/6 : le type de ranking\n"
            "Les nouveaux joueurs commencent avec le rating initial du système choisi."
        ),
        on_pick=on_pick,
    )


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
        fav = parse_int(str(self.favs.value))
        ban = parse_int(str(self.bans.value))
        random_ban = parse_int(str(self.random_bans.value))
        if fav is None or ban is None or random_ban is None:
            await send_step(
                interaction, "Comptes invalides : donne des nombres entiers.", discord.ui.View()
            )
            return
        self.state.set("fav_count", fav)
        self.state.set("ban_count", ban)
        self.state.set("random_ban_count", random_ban)
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
            days = parse_int(raw)
            if days is None:
                await send_step(
                    interaction, "Durée invalide (un nombre de jours).", discord.ui.View()
                )
                return
            self.state.set("days", days)
        await _step_recap(interaction, self.state)


async def _step_recap(interaction: discord.Interaction, state: WizardState) -> None:
    """Show the recap; one button creates the season and opens enrollments."""

    async def on_confirm(inner: discord.Interaction) -> None:
        await _finish(inner, state)

    lines = [
        f"## Récapitulatif de la saison **{state.name}**",
        f"- Map pool : **{state.get('pool_name', '— aucun —') if state.get('pool_id') else '— aucun —'}**",
        f"- Mode de pick : **{state.get('pick_label') or state.get('pick_strategy') or '—'}**",
        f"- Ranking : **{state.get('rating_system') or 'elo'}**",
        f"- Favoris : **{state.get('fav_count')}** / Bans : **{state.get('ban_count')}**"
        f" / Bans aléatoires : **{state.get('random_ban_count')}**",
        f"- Durée : **{state.get('days')} jours**" if state.get("days") else "- Durée : **sans fin programmée**",
        "",
        "Les inscriptions seront **ouvertes** à la création — et peuvent le rester :",
        "c'est un ladder, les joueurs peuvent rejoindre en cours de saison.",
    ]
    await ask_confirm(
        interaction,
        content="\n".join(lines),
        button_label="Créer la saison et ouvrir les inscriptions",
        on_confirm=on_confirm,
    )


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
            state.subject_id,
            state.name,
            state.get("pool_id") or None,
            start_at=now,
            end_at=now + state.get("days") * 86_400_000 if state.get("days") else None,
        )
    except Exception:
        logger.exception("SEASON WIZARD: creation failed")
        await interaction.response.edit_message(content="Création échouée (nom déjà pris ?).")
        return
    await _apply_wizard_settings(interaction, state, wiring)
    try:
        await wiring.service.set_enrollments_open(state.subject_id, True)
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
    if state.get("pick_strategy"):
        changes["pick_strategy"] = state.get("pick_strategy")
    if state.get("rating_system"):
        changes["rating_system"] = state.get("rating_system")
    for key, value in (
        ("player_fav_count", state.get("fav_count")),
        ("player_ban_count", state.get("ban_count")),
        ("random_ban_count", state.get("random_ban_count")),
    ):
        if value is not None:
            changes[key] = value
    if not changes:
        return
    try:
        await wiring.service.admin.update_settings(
            state.subject_id, str(interaction.user.id), changes
        )
    except Exception:
        logger.exception("SEASON WIZARD: settings apply failed")
