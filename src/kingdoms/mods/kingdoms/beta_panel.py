"""Beta start interface (beta-weekend-v2, Drasah 17/10/2026).

``lancer_beta now`` in the console answers with this panel: two
buttons — 🚀 « Maintenant » arms the beta to start at the current
moment, 🕒 « Programmer… » opens a Discord modal where Drasah types
the EXACT start (``JJ/MM HH:MM`` or ``JJ/MM/AAAA HH:MM``, Europe/
Paris). The submission arms the 52h plan from that precise moment
and answers with the full schedule in Paris hours.
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
    },
}


def _strings(locale: Any) -> dict[str, str]:
    lang = "fr" if locale is None or str(locale).startswith("fr") else "en"
    return _STRINGS[lang]


def beta_start_report(report: dict[str, object]) -> str:
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
    """The « Programmer… » modal — one text field for the exact start."""

    def __init__(self, console: Any, locale: Any = None) -> None:
        """Store the console seam and build the input field."""
        strings = _strings(locale)
        super().__init__(title=strings["modal_title"], timeout=300)
        self._console = console
        self._strings = strings
        self.start_input = discord.ui.TextInput(
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


class BetaStartView(discord.ui.View):
    """The two-button beta start panel (« Maintenant » / « Programmer… »)."""

    def __init__(self, console: Any, locale: Any = None) -> None:
        """Store the console seam; the view lives 15 minutes."""
        super().__init__(timeout=900)
        self._console = console
        self._strings = _strings(locale)

    @discord.ui.button(label="🚀", style=discord.ButtonStyle.green)
    async def start_now(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Arm the beta to start at the current moment."""
        del button
        events = getattr(self._console.bot, "kingdoms_events_service", None)
        if events is None:
            err = self._strings["bad_start"].format(error="mod non câblé")
            await interaction.response.send_message(err, ephemeral=True)
            return
        report = await events.activate_beta("now", start_at=game_now())
        await interaction.response.send_message(beta_start_report(report))

    @discord.ui.button(label="🕒", style=discord.ButtonStyle.blurple)
    async def start_scheduled(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Open the modal for the exact start."""
        del button
        await interaction.response.send_modal(BetaStartModal(self._console, None))


def beta_start_panel(console: Any, locale: Any = None) -> tuple[str, BetaStartView]:
    """Return (content, view) for the console's ``lancer_beta now`` answer."""
    strings = _strings(locale)
    return strings["panel_body"], BetaStartView(console, locale)
