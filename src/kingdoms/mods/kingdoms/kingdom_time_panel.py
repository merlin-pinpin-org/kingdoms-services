"""Kingdoms time panel — the Machine à temps salon (chantier E).

``TimeMachineService`` is the game clock; this file is its Discord
surface: one pinned status message in the ``machine-a-temps`` admin
salon whose buttons refresh the clock, fast-forward (+1/+3/+7 days),
jump to the next key moment, rewind one click (the AUTO snapshots the
scheduler takes at every cycle switch, age switch and Lord's Day
close), pause the game (freeze), and walk the whole season back to
its setup phase.

Drasah's readability rules (2026-10-11): the clock must read at a
glance — the phase line says where the season stands (setup, en cours,
terminée, EN PAUSE) and every date reads "dimanche 18 octobre, 23h30"
(Paris time, no year — the year stays in the snapshot ids for the
archives).

Restart-proof like every Kingdoms panel: the buttons are
``DynamicItem`` classes riding ``kingdoms:time:<action>`` custom ids,
the pinned message is refreshed in place via a marker, and every
simulated report is announced in Géopolitique best effort.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import discord

logger = logging.getLogger("kingdoms.time_panel")

TIME_PANEL_MARKER = "<!-- kingdoms-time -->"

_DEFAULT_TZ = "Europe/Paris"

_FR_WEEKDAYS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
_FR_MONTHS = (
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
)
_EN_WEEKDAYS = (
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
)
_EN_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)

_STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "panel_title": "🕰️ **Kingdoms time machine** — the game clock",
        "phase_setup": "🧱 **Setup** — the game has not started yet (button *Start the season* in Settings).",
        "phase_started": "▶️ **Running** — Cycle **{cycle}/{weeks}** · Age **{age}** (Paris time)",
        "phase_ended": "🏁 **Season over** — restore a snapshot to replay it.",
        "frozen_line": "⏸ **PAUSED** — the game clock is frozen (⏯ to resume; the bot catches up on its next tick).",
        "next_cycle_line": "Next cycle switch: {next_cycle}",
        "next_age_line": "Next age switch: {next_age}",
        "season_end_line": "Season verdict: {season_end}",
        "snapshots_line": "Snapshots: {count}",
        "refresh_button": "Refresh",
        "freeze_button": "⏯ Pause/Resume",
        "plus1d_button": "+1 day",
        "plus3d_button": "+3 days",
        "plus7d_button": "+7 days",
        "next_cycle_button": "⏭ Next cycle",
        "next_age_button": "⏭ Next age",
        "season_end_button": "⏭ Season end",
        "prev_cycle_button": "↩ Prev cycle",
        "prev_age_button": "↩ Prev age",
        "prev_lords_day_button": "↩ Lord's Day end",
        "lords_enter_button": "🛡 Open Lord's Day",
        "lords_exit_button": "🛡 Close Lord's Day",
        "back_setup_button": "↩ Back to setup",
        "snapshot_button": "📷 Snapshot",
        "restore_button": "🗂 Restore…",
        "not_allowed": "The time machine is admin-only.",
        "no_service": "The time machine service is not wired.",
        "no_events": "The event service is not wired.",
        "failed": "The time machine action failed: `{0}`.",
        "advance_done": "⏩ Fast-forward of {days} day(s) done ({reports} slot(s) replayed).",
        "jump_done": "⏩ Jump to the key moment done ({reports} slot(s) replayed).",
        "snapshot_done": "📷 Snapshot **{label}** saved.",
        "restore_done": "↩ Snapshot **{label}** restored (the game is PAUSED — ⏯ resumes).",
        "frozen_on": "⏸ **Game paused.** The scheduler replays no slot until you resume.",
        "frozen_off": "▶️ **Time resumed.** The bot catches up on the missed slots on its next tick (~5 min).",
        "prev_done": "↩ Rewound to the latest **{moment}** snapshot ({label}).",
        "lords_enter_done": "🛡 Lord's Day window forced open (cycle {cycle}).",
        "lords_exit_done": "🛡 Lord's Day window closed — closure snapshot saved.",
        "restore_title": "Restore a snapshot (most recent first)",
        "restore_none": "No snapshot yet — take one with 📷 Snapshot or jump first.",
        "restore_row": "{date} — {label}",
        "back_setup_confirm_title": (
            "⚠️ Back to setup? Everything is wiped (players back to the queue, "
            "kingdom salons deleted, NO archive). Snapshots survive. Confirm:"
        ),
        "back_setup_confirm": "✅ Confirm",
        "back_setup_cancel": "❌ Cancel",
        "back_setup_cancelled": "Cancelled — nothing was touched.",
        "back_setup_done": "🧱 Fresh season in setup ({season}) — salons rebuilt ({deleted} deleted).",
        "now_line": "🕰 Current game time: {now}",
        "setup_schedule_line": (
            "📅 Once started: cycle switch every Sunday 23:30 · age switch every"
            " Wednesday 00:00 · {weeks} week season · verdict Sunday 23:59."
        ),
    },
    "fr": {
        "panel_title": "🕰️ **Machine à temps Kingdoms** — l'horloge du jeu",
        "phase_setup": "🧱 **Préparation (setup)** — la partie n'est pas encore démarrée.",
        "phase_started": "▶️ **En cours** — Cycle **{cycle}/{weeks}** · Âge **{age}** (heure de Paris)",
        "phase_ended": "🏁 **Saison terminée** — restaurez un snapshot pour la rejouer.",
        "frozen_line": "⏸ **EN PAUSE** — le temps est gelé (⏯ pour reprendre ; le bot rattrape au tick suivant).",
        "next_cycle_line": "Prochain basculement de cycle : {next_cycle}",
        "next_age_line": "Prochain basculement d'âge : {next_age}",
        "season_end_line": "Verdict de fin de saison : {season_end}",
        "snapshots_line": "Snapshots : {count}",        "refresh_button": "Rafraîchir",
        "freeze_button": "⏯ Pause/Reprendre",
        "plus1d_button": "+1 jour",
        "plus3d_button": "+3 jours",
        "plus7d_button": "+7 jours",
        "next_cycle_button": "⏭ Prochain cycle",
        "next_age_button": "⏭ Prochain âge",
        "season_end_button": "⏭ Fin de saison",
        "prev_cycle_button": "↩ Cycle précédent",
        "prev_age_button": "↩ Âge précédent",
        "prev_lords_day_button": "↩ Fin jour du seigneur",
        "lords_enter_button": "🛡 Ouvrir jour du seigneur",
        "lords_exit_button": "🛡 Fermer jour du seigneur",
        "back_setup_button": "↩ Retour setup",
        "snapshot_button": "📷 Snapshot",
        "restore_button": "🗂 Restaurer…",
        "not_allowed": "La machine à temps est réservée aux admins.",
        "no_service": "Le service machine à temps n'est pas câblé.",
        "no_events": "Le service d'événements n'est pas câblé.",
        "failed": "L'action machine à temps a échoué : `{0}`.",
        "advance_done": "⏩ Avance de {days} jour(s) effectuée ({reports} créneau(x) rejoué(s)).",
        "jump_done": "⏩ Saut au moment clé effectué ({reports} créneau(x) rejoué(s)).",
        "snapshot_done": "📷 Snapshot **{label}** sauvegardé.",
        "restore_done": "↩ Snapshot **{label}** restauré — la partie est EN PAUSE (⏯ pour reprendre).",
        "frozen_on": "⏸ **Partie gelée.** Le scheduler ne rejoue plus aucun créneau jusqu'à reprise.",
        "frozen_off": "▶️ **Temps repris.** Le bot rattrape les créneaux manqués à son prochain tick (~5 min).",
        "prev_done": "↩ Retour au dernier snapshot **{moment}** effectué ({label}).",
        "lords_enter_done": "🛡 Fenêtre jour du seigneur forcée ouverte (cycle {cycle}).",
        "lords_exit_done": "🛡 Fenêtre jour du seigneur fermée — snapshot de clôture sauvegardé.",
        "restore_title": "Restaurer un snapshot (plus récent en premier)",
        "restore_none": "Aucun snapshot — prenez-en un avec 📷 Snapshot ou sautez d'abord.",
        "restore_row": "{date} — {label}",
        "back_setup_confirm_title": (
            "⚠️ Retour en setup ? Tout est effacé (joueurs renvoyés en file d'attente, "
            "salons royaumes supprimés, AUCUNE archive). Les snapshots survivent. Confirmer :"
        ),
        "back_setup_confirm": "✅ Confirmer",
        "back_setup_cancel": "❌ Annuler",
        "back_setup_cancelled": "Annulé — rien n'a été touché.",
        "back_setup_done": "🧱 Saison neuve en setup ({season}) — salons reconstruits ({deleted} supprimés).",
        "now_line": "🕰 Horloge actuelle du jeu : {now}",
        "setup_schedule_line": (
            "📅 Une fois démarrée : basculement de cycle chaque dimanche 23h30 ·"
            " âge chaque mercredi 00h00 · saison de {weeks} semaines · verdict le"
            " dernier dimanche 23h59."
        ),
    },
}


def _strings(locale: str) -> dict[str, str]:
    return _STRINGS["fr"] if str(locale).lower().startswith("fr") else _STRINGS["en"]


def _fmt(moment: Any, locale: str, tz_name: str | None = None) -> str:
    """Render a datetime as Drasah reads it: weekday, day, month, time.

    No year (Drasah's readability rule — the year stays in the
    snapshot ids for the archives), converted to the schedule's
    timezone (Paris): "dimanche 18 octobre, 23h30".
    """
    if moment is None:
        return "—"
    stamp = moment
    if not isinstance(stamp, datetime):
        try:
            stamp = datetime.fromisoformat(str(moment))
        except ValueError:
            return str(moment)
    try:
        stamp = stamp.astimezone(ZoneInfo(tz_name or _DEFAULT_TZ))
    except Exception:  # naive or unknown tz — keep the raw stamp
        logger.debug("time panel: cannot convert %r to tz %r", moment, tz_name)
    if str(locale).lower().startswith("fr"):
        return f"{_FR_WEEKDAYS[stamp.weekday()]} {stamp.day} {_FR_MONTHS[stamp.month - 1]}, {stamp:%Hh%M}"
    return f"{_EN_WEEKDAYS[stamp.weekday()]} {stamp.day} {_EN_MONTHS[stamp.month - 1]}, {stamp:%H:%M}"


def status_text(strings: dict[str, str], status: dict[str, Any], locale: str = "fr") -> str:
    """Render the game-clock status block of the pinned panel."""
    tz_name = status.get("timezone") if isinstance(status.get("timezone"), str) else None
    lines = [strings["panel_title"], ""]
    # Drasah's 2026-10-12 report: the panel showed "no timeline at all"
    # — the CURRENT game time is the anchor every test reads first
    # (lock end, next cycle, everything compares against it).
    lines.append(
        strings["now_line"].format(now=_fmt(status.get("now"), locale, tz_name))
    )
    if status.get("season") is None:
        lines.append(strings["phase_setup"])
        lines.append(strings["setup_schedule_line"].format(weeks=status.get("weeks", "?")))
    elif status.get("finished"):
        lines.append(strings["phase_ended"])
    elif status.get("phase") == "setup":
        lines.append(strings["phase_setup"])
        lines.append(strings["setup_schedule_line"].format(weeks=status.get("weeks", "?")))
    else:
        lines.append(
            strings["phase_started"].format(
                cycle=status.get("cycle", "?"),
                weeks=status.get("weeks", "?"),
                age=str(status.get("age_display", status.get("age", "?"))),
            )
        )
    if status.get("frozen"):
        lines.append(strings["frozen_line"])
    if status.get("season") is not None:
        lines.append(strings["next_cycle_line"].format(
            next_cycle=_fmt(status.get("next_cycle_at"), locale, tz_name)
        ))
        lines.append(strings["next_age_line"].format(
            next_age=_fmt(status.get("next_age_at"), locale, tz_name)
        ))
        lines.append(strings["season_end_line"].format(
            season_end=_fmt(status.get("season_end_at"), locale, tz_name)
        ))
    lines.append(strings["snapshots_line"].format(count=status.get("snapshot_count", 0)))
    lines.append(TIME_PANEL_MARKER)
    return "\n".join(lines)


def build_time_view(locale: str) -> discord.ui.View:
    """Build the persistent time-machine action rows (Drasah's layout).

    Row 1 the clock (refresh, pause, snapshot, restore, back-setup),
    row 2 the fast-forward, row 3 the key-moment jumps, row 4 the
    one-click rewinds, row 5 the Lord's Day window.
    """
    strings = _strings(locale)
    rows: list[list[tuple[str, str, discord.ButtonStyle]]] = [
        [
            ("refresh", strings["refresh_button"], discord.ButtonStyle.secondary),
            ("freeze", strings["freeze_button"], discord.ButtonStyle.secondary),
            ("snapshot", strings["snapshot_button"], discord.ButtonStyle.success),
            ("restore", strings["restore_button"], discord.ButtonStyle.secondary),
            ("back-setup", strings["back_setup_button"], discord.ButtonStyle.danger),
        ],
        [
            ("plus1d", strings["plus1d_button"], discord.ButtonStyle.primary),
            ("plus3d", strings["plus3d_button"], discord.ButtonStyle.primary),
            ("plus7d", strings["plus7d_button"], discord.ButtonStyle.primary),
        ],
        [
            ("next-cycle", strings["next_cycle_button"], discord.ButtonStyle.primary),
            ("next-age", strings["next_age_button"], discord.ButtonStyle.primary),
            ("season-end", strings["season_end_button"], discord.ButtonStyle.danger),
        ],
        [
            ("prev-cycle", strings["prev_cycle_button"], discord.ButtonStyle.secondary),
            ("prev-age", strings["prev_age_button"], discord.ButtonStyle.secondary),
            ("prev-lords-day", strings["prev_lords_day_button"], discord.ButtonStyle.secondary),
        ],
        [
            ("lords-enter", strings["lords_enter_button"], discord.ButtonStyle.primary),
            ("lords-exit", strings["lords_exit_button"], discord.ButtonStyle.secondary),
        ],
    ]
    view = discord.ui.View(timeout=None)
    for row in rows:
        for action, label, style in row:
            view.add_item(KingdomTimeButton(action, label, style))
    return view


async def _channel_messages(channel: discord.TextChannel) -> list[discord.Message]:
    try:
        return [message async for message in channel.history(limit=50)]
    except Exception:
        logger.warning("KINGDOM TIME: channel history read failed", exc_info=True)
        return []


async def _announce_reports_safe(
    guild: discord.Guild | None, locale: str, reports: list[dict[str, Any]]
) -> None:
    """Announce every simulated report in Géopolitique (best effort)."""
    if guild is None or not reports:
        return
    try:
        from kingdoms.mods.kingdoms.kingdom_content import announce_geopolitics
        from kingdoms.mods.kingdoms.kingdom_events_loop import _report_text
        from kingdoms.mods.kingdoms.kingdom_events_loop import _strings as event_strings

        strings = event_strings(locale)
        for report in reports:
            await announce_geopolitics(guild, locale, _report_text(strings, report))
    except Exception:
        logger.info("KINGDOM TIME: simulated report announcement skipped", exc_info=True)


async def _refresh_panel_message_safe(interaction: discord.Interaction) -> None:
    """Edit the pinned panel message in place with the fresh clock."""
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

    wiring = _wiring()
    message = getattr(interaction, "message", None)
    if message is None or wiring.time_service is None:
        return
    try:
        locale = str(interaction.locale) if interaction.locale else "fr"
        status = await wiring.time_service.status()
        await message.edit(content=status_text(_strings(locale), status, locale), view=build_time_view(locale))
    except Exception:
        logger.info("KINGDOM TIME: panel refresh skipped", exc_info=True)


class KingdomTimeRestoreButton(discord.ui.Button[Any]):
    """One ephemeral (session-scoped) restore button — no persistence needed."""

    def __init__(self, snapshot: dict[str, Any], strings: dict[str, str], locale: str) -> None:
        tz_name = snapshot.get("timezone") if isinstance(snapshot.get("timezone"), str) else None
        label = strings["restore_row"].format(
            date=_fmt(snapshot.get("created_at"), locale, tz_name),
            label=str(snapshot.get("label", "?")),
        )[:80]
        super().__init__(label=label, style=discord.ButtonStyle.secondary)
        self.snapshot = snapshot
        self.strings = strings

    async def callback(self, interaction: discord.Interaction) -> None:
        """Restore this snapshot (the game comes back paused)."""
        from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

        wiring = _wiring()
        if wiring.time_service is None:
            await interaction.response.send_message(self.strings["no_service"], ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            result = await wiring.time_service.restore(str(self.snapshot.get("_id")))
        except Exception as exc:
            await interaction.followup.send(
                self.strings["failed"].format(type(exc).__name__), ephemeral=True
            )
            return
        await interaction.followup.send(
            self.strings["restore_done"].format(label=str(result.get("label", "?"))),
            ephemeral=True,
        )


class KingdomTimeRestoreView(discord.ui.View):
    """The ephemeral snapshot list of the 🗂 Restaurer… button."""

    def __init__(self, snapshots: list[dict[str, Any]], strings: dict[str, str], locale: str) -> None:
        super().__init__(timeout=180)
        for snapshot in snapshots[:25]:
            self.add_item(KingdomTimeRestoreButton(snapshot, strings, locale))


class KingdomTimeConfirmView(discord.ui.View):
    """The ephemeral confirm of the destructive ↩ Retour setup button."""

    def __init__(self, strings: dict[str, str]) -> None:
        super().__init__(timeout=180)
        self.strings = strings

    @discord.ui.button(label="✅", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        """Wipe (no archive), relaunch in setup, rebuild the salons."""
        from kingdoms.mods.kingdoms.kingdom_persistent import (
            _purge_and_reinstall,
            _strip_marriage_livery_for_all_lords_safe,
            _wiring,
        )

        await interaction.response.defer(ephemeral=True)
        wiring = _wiring()
        if wiring.time_service is None:
            await interaction.followup.send(self.strings["no_service"], ephemeral=True)
            return
        try:
            # BEFORE the wipe: strip every lord's livery while their
            # documents still exist (the sync pass cannot clean ghosts
            # once the docs are gone — Drasah 2026-10-12).
            guild0 = interaction.guild
            if guild0 is not None:
                await _strip_marriage_livery_for_all_lords_safe(guild0)
            result = await wiring.time_service.back_to_setup()
        except Exception as exc:
            await interaction.followup.send(
                self.strings["failed"].format(type(exc).__name__), ephemeral=True
            )
            return
        guild = interaction.guild
        reinstalled, deleted = (False, 0)
        if guild is not None:
            reinstalled, deleted = await _purge_and_reinstall(guild)
        await interaction.followup.send(
            self.strings["back_setup_done"].format(
                season=str(result.get("season", "?")), deleted=deleted
            ),
            ephemeral=True,
        )
        if reinstalled:
            await _refresh_panel_message_safe(interaction)

    @discord.ui.button(label="❌", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        """Cancel — nothing is touched."""
        await interaction.response.send_message(self.strings["back_setup_cancelled"], ephemeral=True)


class KingdomTimeButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=(
        r"kingdoms:time:(?P<action>refresh|freeze|snapshot|restore|back-setup"
        r"|plus1d|plus3d|plus7d|next-cycle|next-age|season-end"
        r"|prev-cycle|prev-age|prev-lords-day|lords-enter|lords-exit)"
    ),
):
    """The restart-proof time-machine buttons (custom_id §3b contract)."""

    def __init__(self, action: str, label: str, style: discord.ButtonStyle) -> None:
        super().__init__(discord.ui.Button(label=label, custom_id=f"kingdoms:time:{action}", style=style))
        self.action = action

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomTimeButton:
        """Rebuild the time button from the wire."""
        locale = str(interaction.locale) if interaction.locale else "fr"
        strings = _strings(locale)
        action = match.group("action")
        labels = {
            "refresh": strings["refresh_button"],
            "freeze": strings["freeze_button"],
            "snapshot": strings["snapshot_button"],
            "restore": strings["restore_button"],
            "back-setup": strings["back_setup_button"],
            "plus1d": strings["plus1d_button"],
            "plus3d": strings["plus3d_button"],
            "plus7d": strings["plus7d_button"],
            "next-cycle": strings["next_cycle_button"],
            "next-age": strings["next_age_button"],
            "season-end": strings["season_end_button"],
            "prev-cycle": strings["prev_cycle_button"],
            "prev-age": strings["prev_age_button"],
            "prev-lords-day": strings["prev_lords_day_button"],
            "lords-enter": strings["lords_enter_button"],
            "lords-exit": strings["lords_exit_button"],
        }
        styles = {
            "refresh": discord.ButtonStyle.secondary,
            "freeze": discord.ButtonStyle.secondary,
            "snapshot": discord.ButtonStyle.success,
            "restore": discord.ButtonStyle.secondary,
            "back-setup": discord.ButtonStyle.danger,
            "plus1d": discord.ButtonStyle.primary,
            "plus3d": discord.ButtonStyle.primary,
            "plus7d": discord.ButtonStyle.primary,
            "next-cycle": discord.ButtonStyle.primary,
            "next-age": discord.ButtonStyle.primary,
            "season-end": discord.ButtonStyle.danger,
            "prev-cycle": discord.ButtonStyle.secondary,
            "prev-age": discord.ButtonStyle.secondary,
            "prev-lords-day": discord.ButtonStyle.secondary,
            "lords-enter": discord.ButtonStyle.primary,
            "lords-exit": discord.ButtonStyle.secondary,
        }
        return cls(action, labels[action], styles[action])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Guard the click, then run the action's handler."""
        from kingdoms.mods.kingdoms.kingdom_persistent import _is_admin, _wiring

        strings = _strings(str(interaction.locale) if interaction.locale else "fr")
        wiring = _wiring()
        if not _is_admin(interaction, wiring.bot_admins):
            await interaction.response.send_message(strings["not_allowed"], ephemeral=True)
            return
        if wiring.time_service is None and self.action != "refresh":
            await interaction.response.send_message(strings["no_service"], ephemeral=True)
            return
        if self.action in {"plus1d", "plus3d", "plus7d"}:
            await _action_advance(interaction, strings, self.action)
            return
        handler = _HANDLERS.get(self.action)
        if handler is None:
            return
        await handler(interaction, strings)


# ----------------------------------------------------------------------
# The action handlers (one per button — keeps callback complexity flat)
# ----------------------------------------------------------------------
async def _action_refresh(interaction: discord.Interaction, strings: dict[str, str]) -> None:
    """Show the fresh clock in the ephemeral reply, refresh the panel."""
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

    wiring = _wiring()
    await interaction.response.defer(ephemeral=True)
    if wiring.time_service is None:
        await interaction.followup.send(strings["no_service"], ephemeral=True)
        return
    status = await wiring.time_service.status()
    locale = str(interaction.locale) if interaction.locale else "fr"
    await interaction.followup.send(status_text(strings, status, locale), ephemeral=True)
    await _refresh_panel_message_safe(interaction)


async def _action_freeze(interaction: discord.Interaction, strings: dict[str, str]) -> None:
    """Toggle the pause: freeze the game clock, or resume it."""
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

    wiring = _wiring()
    await interaction.response.defer(ephemeral=True)
    status = await wiring.time_service.status()
    was_frozen = bool(status.get("frozen"))
    try:
        if was_frozen:
            await wiring.time_service.unfreeze()
        else:
            await wiring.time_service.freeze()
    except Exception as exc:
        await interaction.followup.send(strings["failed"].format(type(exc).__name__), ephemeral=True)
        return
    await interaction.followup.send(strings["frozen_off"] if was_frozen else strings["frozen_on"], ephemeral=True)
    await _refresh_panel_message_safe(interaction)


async def _action_snapshot(interaction: discord.Interaction, strings: dict[str, str]) -> None:
    """Photograph the current full state under the manual label."""
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

    wiring = _wiring()
    await interaction.response.defer(ephemeral=True)
    try:
        snapshot = await wiring.time_service.create_snapshot("manuel")
    except Exception as exc:
        await interaction.followup.send(strings["failed"].format(type(exc).__name__), ephemeral=True)
        return
    await interaction.followup.send(
        strings["snapshot_done"].format(label=str(snapshot.get("label", "manuel"))),
        ephemeral=True,
    )
    await _refresh_panel_message_safe(interaction)


async def _action_restore(interaction: discord.Interaction, strings: dict[str, str]) -> None:
    """List the snapshots as an ephemeral restore menu."""
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

    wiring = _wiring()
    await interaction.response.defer(ephemeral=True)
    snapshots = await wiring.time_service.snapshots()
    if not snapshots:
        await interaction.followup.send(strings["restore_none"], ephemeral=True)
        return
    locale = str(interaction.locale) if interaction.locale else "fr"
    await interaction.followup.send(
        strings["restore_title"],
        view=KingdomTimeRestoreView(snapshots, strings, locale),
        ephemeral=True,
    )


async def _action_back_setup(interaction: discord.Interaction, strings: dict[str, str]) -> None:
    """Ask for the destructive confirmation before the wipe."""
    await interaction.response.send_message(
        strings["back_setup_confirm_title"], view=KingdomTimeConfirmView(strings), ephemeral=True
    )


async def _action_advance(
    interaction: discord.Interaction, strings: dict[str, str], action: str
) -> None:
    """Fast-forward +1/+3/+7 days: snapshot, replay, announce."""
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

    wiring = _wiring()
    days = int(action.removeprefix("plus").removesuffix("d"))
    await interaction.response.defer(ephemeral=True)
    try:
        result = await wiring.time_service.advance(days)
    except Exception as exc:
        await interaction.followup.send(strings["failed"].format(type(exc).__name__), ephemeral=True)
        return
    await interaction.followup.send(
        strings["advance_done"].format(days=days, reports=len(result.get("reports", []) or [])),
        ephemeral=True,
    )
    locale = str(interaction.locale) if interaction.locale else "fr"
    await _announce_reports_safe(interaction.guild, locale, list(result.get("reports", []) or []))
    await _refresh_panel_message_safe(interaction)


async def _do_jump(interaction: discord.Interaction, strings: dict[str, str], moment: str) -> None:
    """Jump to a key moment: snapshot, replay, announce."""
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

    wiring = _wiring()
    await interaction.response.defer(ephemeral=True)
    try:
        result = await wiring.time_service.jump_to(moment)
    except Exception as exc:
        await interaction.followup.send(strings["failed"].format(type(exc).__name__), ephemeral=True)
        return
    await interaction.followup.send(
        strings["jump_done"].format(reports=len(result.get("reports", []) or [])),
        ephemeral=True,
    )
    locale = str(interaction.locale) if interaction.locale else "fr"
    await _announce_reports_safe(interaction.guild, locale, list(result.get("reports", []) or []))
    await _refresh_panel_message_safe(interaction)


async def _action_next_cycle(interaction: discord.Interaction, strings: dict[str, str]) -> None:
    """Jump to the next Sunday 23:30 cycle switch."""
    await _do_jump(interaction, strings, "next_cycle")


async def _action_next_age(interaction: discord.Interaction, strings: dict[str, str]) -> None:
    """Jump to the next Wednesday-midnight age switch."""
    await _do_jump(interaction, strings, "next_age")


async def _action_season_end(interaction: discord.Interaction, strings: dict[str, str]) -> None:
    """Jump to the 23:59 verdict of the season's last Sunday."""
    await _do_jump(interaction, strings, "season_end")


async def _do_rewind(interaction: discord.Interaction, strings: dict[str, str], moment: str) -> None:
    """One-click rewind to the latest snapshot of one key moment."""
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

    wiring = _wiring()
    await interaction.response.defer(ephemeral=True)
    try:
        result = await wiring.time_service.restore_last(moment)
    except Exception as exc:
        await interaction.followup.send(strings["failed"].format(type(exc).__name__), ephemeral=True)
        return
    await interaction.followup.send(
        strings["prev_done"].format(moment=moment, label=str(result.get("label", "?"))),
        ephemeral=True,
    )
    await _refresh_panel_message_safe(interaction)


async def _action_prev_cycle(interaction: discord.Interaction, strings: dict[str, str]) -> None:
    """Rewind to the exact moment before the last cycle switch."""
    await _do_rewind(interaction, strings, "cycle")


async def _action_prev_age(interaction: discord.Interaction, strings: dict[str, str]) -> None:
    """Rewind to the exact moment before the last age switch."""
    await _do_rewind(interaction, strings, "age")


async def _action_prev_lords_day(interaction: discord.Interaction, strings: dict[str, str]) -> None:
    """Rewind to the exact moment the Lord's Day window closed."""
    await _do_rewind(interaction, strings, "lords_day_end")


async def _action_lords_enter(interaction: discord.Interaction, strings: dict[str, str]) -> None:
    """Force the Lord's Day window open (the old Paramètres seam)."""
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

    wiring = _wiring()
    if wiring.events_service is None:
        await interaction.response.send_message(strings["no_events"], ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        report = dict(await wiring.events_service.enter_lords_day_phase())
    except Exception as exc:
        await interaction.followup.send(strings["failed"].format(type(exc).__name__), ephemeral=True)
        return
    report["kind"] = "cycle"
    await interaction.followup.send(
        strings["lords_enter_done"].format(cycle=report.get("cycle", "?")), ephemeral=True
    )
    locale = str(interaction.locale) if interaction.locale else "fr"
    await _announce_reports_safe(interaction.guild, locale, [report])
    await _refresh_panel_message_safe(interaction)


async def _action_lords_exit(interaction: discord.Interaction, strings: dict[str, str]) -> None:
    """Close the forced window and snapshot the closure moment."""
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

    wiring = _wiring()
    if wiring.events_service is None:
        await interaction.response.send_message(strings["no_events"], ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        await wiring.events_service.exit_lords_day_phase()
        if wiring.time_service is not None:
            await wiring.time_service.create_snapshot(
                "fin jour du seigneur (forçage)", moment="lords_day_end"
            )
    except Exception as exc:
        await interaction.followup.send(strings["failed"].format(type(exc).__name__), ephemeral=True)
        return
    await interaction.followup.send(strings["lords_exit_done"], ephemeral=True)
    await _refresh_panel_message_safe(interaction)


_HANDLERS = {
    "refresh": _action_refresh,
    "freeze": _action_freeze,
    "snapshot": _action_snapshot,
    "restore": _action_restore,
    "back-setup": _action_back_setup,
    "next-cycle": _action_next_cycle,
    "next-age": _action_next_age,
    "season-end": _action_season_end,
    "prev-cycle": _action_prev_cycle,
    "prev-age": _action_prev_age,
    "prev-lords-day": _action_prev_lords_day,
    "lords-enter": _action_lords_enter,
    "lords-exit": _action_lords_exit,
}


async def deploy_time_panel(channel: discord.TextChannel, locale: str) -> None:
    """Pin (or refresh) the time-machine status message in its salon."""
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

    wiring = _wiring()
    strings = _strings(locale)
    for message in await _channel_messages(channel):
        if TIME_PANEL_MARKER in (message.content or ""):
            try:
                await message.delete()
            except Exception:
                logger.warning("KINGDOM TIME: old panel removal failed", exc_info=True)
    status: dict[str, Any] = {}
    if wiring.time_service is not None:
        try:
            status = await wiring.time_service.status()
        except Exception:
            logger.warning("KINGDOM TIME: status read failed", exc_info=True)
    content = status_text(strings, status, locale)
    try:
        await channel.send(content=content, view=build_time_view(locale))
    except Exception:
        logger.warning("KINGDOM TIME: panel send failed", exc_info=True)
