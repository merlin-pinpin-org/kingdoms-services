"""Beta start interface (beta-weekend-v2, Drasah 17/10/2026).

The ⚙️ Paramètres « Démarrer la beta » button opens this panel
(console-removal-v1): two buttons — 🚀 « Maintenant » arms the beta
to start at the current moment, 🕒 « Programmer… » opens a Discord
modal where Drasah types the EXACT start (``JJ/MM HH:MM`` or ``JJ/MM/
AAAA HH:MM``, Europe/Paris). The submission arms the 52h plan from
that precise moment and answers with the full schedule in Paris
hours. beta-auto-season-v1 (Drasah 25/10): once the beta is armed the
SEASON starts on its own — no second « Démarrer la saison » click
(drafts revealed, territories drawn, realm views refreshed).
"""
from __future__ import annotations

import logging
from typing import Any

import discord

from kingdoms.mods.kingdoms.beta_weekend import parse_beta_start
from kingdoms.mods.kingdoms.game_clock import game_now

logger = logging.getLogger("kingdoms.mods.kingdoms")

_STRINGS = {
    "fr": {
        "panel_title": "🚀 Beta « now » — définition du départ",
        "panel_body": (
            "Choisis le moment EXACT de départ de la beta (52 h) :\n"
            "• 🚀 **Maintenant** — la beta démarre immédiatement\n"
            "• 🕒 **Programmer…** — saisis le départ (JJ/MM HH:MM, ex. 23/10 20:00)"
        ),
        "now_label": "🚀 Maintenant",
        "now_help": "Démarrer la beta maintenant",
        "plan_label": "🕒 Programmer…",
        "plan_help": "Définir le départ exact (JJ/MM HH:MM)",
        "modal_title": "Départ de la beta",
        "modal_label": "Date et heure de départ",
        "modal_placeholder": "23/10 20:00",
        "bad_start": "⚠️ {error}",
        "auto_started": "✅ Saison lancée automatiquement — drafts révélés, territoires distribués, vues du royaume actualisées.",
        "auto_already": "ℹ️ La saison était déjà lancée — rien à faire.",
        "auto_failed": "⚠️ Lancement automatique de la saison échoué (`{error}`) — utilise « Démarrer la saison » sur ⚙️ Paramètres.",
        "auto_no_service": 
"⚠️ Lancement automatique impossible — le service saisons n'est pas branché (utilise « Démarrer la saison »).",
    },
    "en": {
        "panel_title": "🚀 Beta “now” — start definition",
        "panel_body": (
            "Pick the EXACT beta start (52 h):\n"
            "• 🚀 **Now** — the beta starts immediately\n"
            "• 🕒 **Schedule…** — type the start (DD/MM HH:MM, e.g. 23/10 20:00)"
        ),
        "now_label": "🚀 Now",
        "now_help": "Start the beta right now",
        "plan_label": "🕒 Schedule…",
        "plan_help": "Define the exact start (DD/MM HH:MM)",
        "modal_title": "Beta start",
        "modal_label": "Start date and time",
        "modal_placeholder": "23/10 20:00",
        "bad_start": "⚠️ {error}",
        "auto_started": "✅ Season started automatically — drafts revealed, territories drawn, realm views refreshed.",
        "auto_already": "ℹ️ The season was already started — nothing to do.",
        "auto_failed": "⚠️ Automatic season start failed (`{error}`) — use « Start the season » on ⚙️ Settings.",
        "auto_no_service": "⚠️ Automatic start impossible — the season service is not wired (use « Start the season »).",
    },
}


def _strings(locale: Any) -> dict[str, str]:
    lang = "fr" if locale is None or str(locale).startswith("fr") else "en"
    return _STRINGS[lang]


async def _auto_start_season_safe(console: Any, interaction: discord.Interaction, strings: dict[str, str]) -> str:
    """beta-auto-season-v1 (Drasah 25/10): start the season right after
    the beta is armed — one click instead of two. The kingdoms service
    is idempotent (a no-op when the phase is not ``setup``), so this
    is safe on every path; the reveal + status refresh are best
    effort (they live in kingdom_persistent)."""
    kingdoms = getattr(console.bot, "kingdoms_service", None)
    if kingdoms is None:
        return strings["auto_no_service"]
    try:
        season = await kingdoms.current_season()
    except Exception:
       
 season = None
    if season is None:
        return strings["auto_no_service"]
    already = getattr(season, "phase", "setup") != "setup"
    try:
        await kingdoms.start_season()
    except Exception as exc:
        return strings["auto_failed"].format(error=getattr(exc, "message", None) or type(exc).__name__)
    if already:
        return strings["auto_already"]
    guild = getattr(interaction, "guild", None)
    locale = str(interaction.locale) if interaction.locale else "en"
    try:
        from kingdoms.mods.kingdoms.kingdom_persistent import (
            _ensure_realms_after_launch,
            _refresh_season_status_safe,
        )

        if guild is not None:
            await _ensure_realms_after_launch(guild, locale)
            await _refresh_season_status_safe(guild, locale)
            # mercenary-beta-refresh-v1: same refresh inside the auto
            # start path (covers the direct launch even if the caller's
            # own refresh is skipped).
            from kingdoms.mods.kingdoms.kingdom_mercenary_panel import (
                refresh_mercenary_panels_safe,
            )

            await refresh_mercenary_panels_safe(guild, locale)
    except Exception:
        logger.warning("BETA PANEL: post-launch refresh skipped", exc_info=True)
    return strings["auto_started"]


async def _refresh_taverne_mercenary_safe(interaction: discord.Interaction) -> None:
    """mercenary-beta-refresh-v1 (Drasah 25/10): re-render the Chief's
    taverne panel right after a beta arm/start — the beta call button
    rides the view only while a beta is armed/running, and without this
    refresh the button would wait for the next market open (20h) to
    appear."""
    guild = getattr(interaction, "guild", None)
    if guild is None:
        return
    locale = str(interaction.locale) if interaction.locale else "en"
    try:
        from kingdoms.mods.kingdoms.kingdom_mercenary_panel import (
            refresh_mercenary_panels_safe,
        )

        await refresh_mercenary_panels_safe(guild, locale)
    except Exception:
        logger.warning("BETA PANEL: mercenary panel refresh skipped", exc_info=True)


def beta_start_report(report: dict[str, Any]) -> str:
    """Format the armed plan as a console answer (Paris hours)."""
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Europe/Paris")
    fmt = "%d/%m %H:%M"
    start = report["start_at"]
    header = (
        f"🚀 Beta « {report['name']} » ARMÉE — départ le {start.astimezone(tz).strftime(fmt)}"
        if report.get("armed")
        else f"🚀 Beta « {report['name']} » lancée — départ immédiat"
    )
    lines = [header]
    lines.append(f"• passage d'âge : {report['age_at'].astimezone(tz).strftime(fmt)}")
    cycles = ", ".join(slot.astimezone(tz).strftime(fmt) for slot in report["cycle_slots"])
    lines.append(f"• cycles Jour du Seigneur : {cycles}")
    fin = report["end_at"].astimezone(tz).strftime(fmt)
    lines.append(f"• fin de saison officielle : {fin} (résultats + récompenses)")
    lines.append("• rythme : 2 attaques / 2 défenses · délais 10 min · actions spéciales gratuites")
    return "\n".join(lines)


class BetaStartModal(discord.ui.Modal):
    """The « Programmer…
 » modal — one text field for the exact start."""

    def __init__(self, console: Any, locale: Any = None) -> None:
        """Store the console seam and build the input field."""
        strings = _strings(locale)
        super().__init__(title=strings["modal_title"], timeout=300)
        self._console = console
        self._strings = strings
        self.start_input: discord.ui.TextInput[BetaStartModal] = discord.ui.TextInput(
            label=strings["modal_label"],
            placeholder=strings["modal_placeholder"],
            min_length=4,
            max_length=20,
        )
        self.add_item(self.start_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Parse the typed start, arm the beta, answer the plan."""
        events = getattr(self._console.bot, "kingdoms_events_service", None)
        if events is None:
            err = self._strings["bad_start"].format(error="mod non câblé")
            await interaction.response.send_message(err, ephemeral=True)
            return
        try:
            start = parse_beta_start(str(self.start_input.value or ""), game_now())
            report = await events.activate_beta("now", start_at=start)
        except ValueError as error:
            await interaction.response.send_message(self._strings["bad_start"].format(error=error), ephemeral=True)
            return
        await interaction.response.send_message(beta_start_report(report))
        # beta-auto-season-v1: the season starts on its own — no second
        # click on « Démarrer la saison ».
        note = await _auto_start_season_safe(self._console, interaction, self._strings)
        await interaction.followup.send(note)
        # mercenary-beta-refresh-v1: the taverne panel shows the beta
        # call button immediately after the arm — not at the 20h open.
        await _refresh_taverne_mercenary_safe(interaction)


class BetaStartView(discord.ui.View):
    """The two-button beta start panel (« Maintenant » / « Programmer… »)."""

    def __init__(self, console: Any, locale: Any = None) -> None:
        """Store the console seam; the view lives 15 minutes."""
        super().__init__(timeout=900)
  
      self._console = console
        self._strings = _strings(locale)

    @discord.ui.button(label="🚀", style=discord.ButtonStyle.green)
    async def start_now(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        """Arm the beta to start at the current moment."""
        del button
        events = getattr(self._console.bot, "kingdoms_events_service", None)
        if events is None:
            err = self._strings["bad_start"].format(error="mod non câblé")
            await interaction.response.send_message(err, ephemeral=True)
            return
        report = await events.activate_beta("now", start_at=game_now())
        await interaction.response.send_message(beta_start_report(report))
        # beta-auto-season-v1: the season starts on its own — no second
        # click on « Démarrer la saison ».
        note = await _auto_start_season_safe(self._console, interaction, self._strings)
        await interaction.followup.send(note)
        # mercenary-beta-refresh-v1: the taverne panel shows the beta
        # call button immediately after the arm — not at the 20h open.
        await _refresh_taverne_mercenary_safe(interaction)

    @discord.ui.button(label="🕒", style=discord.ButtonStyle.blurple)
    async def start_scheduled(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        """Open the modal for the exact start."""
        del button
        await interaction.response.send_modal(BetaStartModal(self._console, None))


def beta_start_panel(console: Any, locale: Any = None) -> tuple[str, BetaStartView]:
    """Return (content, view) for the console's ``lancer_beta now`` answer."""
    strings = _strings(locale)
    return strings["panel_body"], BetaStartView(console, locale)
