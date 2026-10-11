"""Kingdoms time panel — the Machine à temps salon (chantier E).

``TimeMachineService`` is the game clock Drasah asked for (see the
time-machine module); this file is its Discord surface: one pinned
status message in a dedicated admin salon (``machine-a-temps``) whose
buttons refresh the clock, fast-forward (+1/+3/+7 days), jump to the
next key moment (cycle switch, age switch, season end) and take or
restore snapshots.

Restart-proof like every Kingdoms panel: the buttons are
``DynamicItem`` classes riding ``kingdoms:time:<action>`` custom ids
(the §3b reconstruction contract), the pinned message is refreshed in
place via a marker, and every simulated report is announced in
Géopolitique best effort — a fast-forward announces exactly what the
real calendar would have announced.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import discord

logger = logging.getLogger("kingdoms.time_panel")

TIME_PANEL_MARKER = "<!-- kingdoms-time -->"
TIME_PANEL_CHANNEL = "machine-a-temps"

_STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "panel_title": "🕰️ **Kingdoms time machine** — the game clock",
        "no_season": "No season is running: launch one from the Paramètres panel first.",
        "cycle_line": "Cycle **{cycle}/{weeks}** · age **{age}** · phase **{phase}**",
        "finished_line": "The season is **finished** (phase {phase}) — restore a snapshot to replay it.",
        "next_cycle_line": "Next cycle switch: {next_cycle}",
        "next_age_line": "Next age switch: {next_age}",
        "season_end_line": "Season verdict: {season_end}",
        "snapshots_line": "Snapshots: {count}",
        "refresh_button": "Refresh",
        "plus1d_button": "+1 day",
        "plus3d_button": "+3 days",
        "plus7d_button": "+7 days",
        "next_cycle_button": "⏭ Next cycle",
        "next_age_button": "⏭ Next age",
        "season_end_button": "⏭ Season end",
        "snapshot_button": "📷 Snapshot",
        "restore_button": "↩ Restore…",
        "not_allowed": "The time machine is admin-only.",
        "no_service": "The time machine service is not wired.",
        "failed": "The time machine action failed: `{0}`.",
        "advance_done": "⏩ Fast-forward of {days} day(s) done ({reports} slot(s) replayed).",
        "jump_done": "⏩ Jump to the key moment done ({reports} slot(s) replayed).",
        "snapshot_done": "📷 Snapshot **{label}** saved.",
        "restore_done": "↩ Snapshot **{label}** restored (backup {backup}).",
        "restore_title": "Restore a snapshot (most recent first)",
        "restore_none": "No snapshot yet — take one with 📷 Snapshot or jump first.",
        "restore_row": "{date} — {label}",
    },
    "fr": {
        "panel_title": "🕰️ **Machine à temps Kingdoms** — l'horloge du jeu",
        "no_season": "Aucune saison en cours : lancez-en une depuis le panneau Paramètres.",
        "cycle_line": "Cycle **{cycle}/{weeks}** · âge **{age}** · phase **{phase}**",
        "finished_line": "La saison est **terminée** (phase {phase}) — restaurez un snapshot pour la rejouer.",
        "next_cycle_line": "Prochain basculement de cycle : {next_cycle}",
        "next_age_line": "Prochain basculement d'âge : {next_age}",
        "season_end_line": "Verdict de fin de saison : {season_end}",
        "snapshots_line": "Snapshots : {count}",
        "refresh_button": "Rafraîchir",
        "plus1d_button": "+1 jour",
        "plus3d_button": "+3 jours",
        "plus7d_button": "+7 jours",
        "next_cycle_button": "⏭ Prochain cycle",
        "next_age_button": "⏭ Prochain âge",
        "season_end_button": "⏭ Fin de saison",
        "snapshot_button": "📷 Snapshot",
        "restore_button": "↩ Restaurer…",
        "not_allowed": "La machine à temps est réservée aux admins.",
        "no_service": "Le service machine à temps n'est pas câblé.",
        "failed": "L'action machine à temps a échoué : `{0}`.",
        "advance_done": "⏩ Avance de {days} jour(s) effectuée ({reports} créneau(x) rejoué(s)).",
        "jump_done": "⏩ Saut au moment clé effectué ({reports} créneau(x) rejoué(s)).",
        "snapshot_done": "📷 Snapshot **{label}** sauvegardé.",
        "restore_done": "↩ Snapshot **{label}** restauré (sauvegarde {backup}).",
        "restore_title": "Restaurer un snapshot (plus récent en premier)",
        "restore_none": "Aucun snapshot — prenez-en un avec 📷 Snapshot ou sautez d'abord.",
        "restore_row": "{date} — {label}",
    },
}


def _strings(locale: str) -> dict[str, str]:
    return _STRINGS["fr"] if str(locale).lower().startswith("fr") else _STRINGS["en"]


def _fmt(moment: Any) -> str:
    """Render an optional datetime as a short local line."""
    if moment is None:
        return "—"
    return f"{moment:%Y-%m-%d %H:%M} UTC"


def status_text(strings: dict[str, str], status: dict[str, Any]) -> str:
    """Render the game-clock status block of the pinned panel."""
    lines = [strings["panel_title"], ""]
    if status.get("season") is None:
        lines.append(strings["no_season"])
    else:
        if status.get("finished"):
            lines.append(
                strings["finished_line"].format(phase=str(status.get("phase", "?")))
            )
        else:
            lines.append(
                strings["cycle_line"].format(
                    cycle=status.get("cycle", "?"),
                    weeks=status.get("weeks", "?"),
                    age=str(status.get("age", "?")),
                    phase=str(status.get("phase", "?")),
                )
            )
            lines.append(strings["next_cycle_line"].format(next_cycle=_fmt(status.get("next_cycle_at"))))
            lines.append(strings["next_age_line"].format(next_age=_fmt(status.get("next_age_at"))))
        lines.append(strings["season_end_line"].format(season_end=_fmt(status.get("season_end_at"))))
    lines.append(strings["snapshots_line"].format(count=status.get("snapshot_count", 0)))
    lines.append(TIME_PANEL_MARKER)
    return "\n".join(lines)


def build_time_view(locale: str) -> discord.ui.View:
    """Build the persistent time-machine action rows."""
    strings = _strings(locale)
    view = discord.ui.View(timeout=None)
    view.add_item(KingdomTimeButton("refresh", strings["refresh_button"], discord.ButtonStyle.secondary))
    view.add_item(KingdomTimeButton("plus1d", strings["plus1d_button"], discord.ButtonStyle.primary))
    view.add_item(KingdomTimeButton("plus3d", strings["plus3d_button"], discord.ButtonStyle.primary))
    view.add_item(KingdomTimeButton("plus7d", strings["plus7d_button"], discord.ButtonStyle.primary))
    view.add_item(KingdomTimeButton("next-cycle", strings["next_cycle_button"], discord.ButtonStyle.primary))
    view.add_item(KingdomTimeButton("next-age", strings["next_age_button"], discord.ButtonStyle.primary))
    view.add_item(KingdomTimeButton("season-end", strings["season_end_button"], discord.ButtonStyle.danger))
    view.add_item(KingdomTimeButton("snapshot", strings["snapshot_button"], discord.ButtonStyle.success))
    view.add_item(KingdomTimeButton("restore", strings["restore_button"], discord.ButtonStyle.secondary))
    return view


async def _channel_messages(channel: discord.TextChannel) -> list[discord.Message]:
    try:
        return [message async for message in channel.history(limit=50)]
    except Exception:
        logger.warning("KINGDOM TIME: channel history read failed", exc_info=True)
        return []


async def _announce_reports_safe(guild: discord.Guild | None, locale: str, reports: list[dict[str, Any]]) -> None:
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
        strings = _strings(str(interaction.locale) if interaction.locale else "en")
        status = await wiring.time_service.status()
        await message.edit(content=status_text(strings, status), view=build_time_view("fr"))
    except Exception:
        logger.info("KINGDOM TIME: panel refresh skipped", exc_info=True)


class KingdomTimeRestoreButton(discord.ui.Button[Any]):
    """One ephemeral (session-scoped) restore button — no persistence needed."""

    def __init__(self, snapshot: dict[str, Any], strings: dict[str, str]) -> None:
        label = strings["restore_row"].format(
            date=_fmt(snapshot.get("created_at")), label=str(snapshot.get("label", "?"))
        )[:80]
        super().__init__(label=label, style=discord.ButtonStyle.secondary)
        self.snapshot = snapshot
        self.strings = strings

    async def callback(self, interaction: discord.Interaction) -> None:
        """Restore this snapshot, best effort."""
        from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

        wiring = _wiring()
        if wiring.time_service is None:
            await interaction.response.send_message(self.strings["no_service"], ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            result = await wiring.time_service.restore(str(self.snapshot.get("_id")))
        except Exception as exc:
            await interaction.followup.send(self.strings["failed"].format(type(exc).__name__), ephemeral=True)
            return
        await interaction.followup.send(
            self.strings["restore_done"].format(
                label=str(result.get("label", "?")), backup=str(result.get("backup", "?"))
            ),
            ephemeral=True,
        )
        await _refresh_panel_message_safe(interaction)


class KingdomTimeRestoreView(discord.ui.View):
    """The ephemeral snapshot list of the ↩ Restaurer… button."""

    def __init__(self, snapshots: list[dict[str, Any]], strings: dict[str, str]) -> None:
        super().__init__(timeout=180)
        for snapshot in snapshots[:25]:
            self.add_item(KingdomTimeRestoreButton(snapshot, strings))


class KingdomTimeButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:time:(?P<action>refresh|plus1d|plus3d|plus7d|next-cycle|next-age|season-end|snapshot|restore)",
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
        locale = str(interaction.locale) if interaction.locale else "en"
        strings = _strings(locale)
        action = match.group("action")
        labels = {
            "refresh": strings["refresh_button"],
            "plus1d": strings["plus1d_button"],
            "plus3d": strings["plus3d_button"],
            "plus7d": strings["plus7d_button"],
            "next-cycle": strings["next_cycle_button"],
            "next-age": strings["next_age_button"],
            "season-end": strings["season_end_button"],
            "snapshot": strings["snapshot_button"],
            "restore": strings["restore_button"],
        }
        styles = {
            "refresh": discord.ButtonStyle.secondary,
            "plus1d": discord.ButtonStyle.primary,
            "plus3d": discord.ButtonStyle.primary,
            "plus7d": discord.ButtonStyle.primary,
            "next-cycle": discord.ButtonStyle.primary,
            "next-age": discord.ButtonStyle.primary,
            "season-end": discord.ButtonStyle.danger,
            "snapshot": discord.ButtonStyle.success,
            "restore": discord.ButtonStyle.secondary,
        }
        return cls(action, labels[action], styles[action])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Run the time action: guard, resolve, execute, announce, refresh."""
        from kingdoms.mods.kingdoms.kingdom_persistent import _is_admin, _wiring

        strings = _strings(str(interaction.locale) if interaction.locale else "en")
        wiring = _wiring()
        if not _is_admin(interaction, wiring.bot_admins):
            await interaction.response.send_message(strings["not_allowed"], ephemeral=True)
            return
        if wiring.time_service is None:
            await interaction.response.send_message(strings["no_service"], ephemeral=True)
            return
        if self.action == "refresh":
            await interaction.response.defer(ephemeral=True)
            status = await wiring.time_service.status()
            await interaction.followup.send(status_text(strings, status), ephemeral=True)
            await _refresh_panel_message_safe(interaction)
            return
        if self.action == "restore":
            await interaction.response.defer(ephemeral=True)
            snapshots = await wiring.time_service.snapshots()
            if not snapshots:
                await interaction.followup.send(strings["restore_none"], ephemeral=True)
                return
            await interaction.followup.send(
                strings["restore_title"],
                view=KingdomTimeRestoreView(snapshots, strings),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            if self.action == "snapshot":
                snapshot = await wiring.time_service.create_snapshot("manuel")
                await interaction.followup.send(
                    strings["snapshot_done"].format(label=str(snapshot.get("label", "manuel"))),
                    ephemeral=True,
                )
                await _refresh_panel_message_safe(interaction)
                return
            if self.action in {"plus1d", "plus3d", "plus7d"}:
                days = int(self.action.removeprefix("plus").removesuffix("d"))
                result = await wiring.time_service.advance(days)
                await interaction.followup.send(
                    strings["advance_done"].format(days=days, reports=len(result.get("reports", []) or [])),
                    ephemeral=True,
                )
            else:
                moment = {
                    "next-cycle": "next_cycle",
                    "next-age": "next_age",
                    "season-end": "season_end",
                }[self.action]
                result = await wiring.time_service.jump_to(moment)
                await interaction.followup.send(
                    strings["jump_done"].format(reports=len(result.get("reports", []) or [])),
                    ephemeral=True,
                )
        except Exception as exc:
            await interaction.followup.send(strings["failed"].format(type(exc).__name__), ephemeral=True)
            return
        locale = str(interaction.locale) if interaction.locale else "en"
        await _announce_reports_safe(interaction.guild, locale, list(result.get("reports", []) or []))
        await _refresh_panel_message_safe(interaction)


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
    content = status_text(strings, status)
    try:
        await channel.send(content=content, view=build_time_view(locale))
    except Exception:
        logger.warning("KINGDOM TIME: panel send failed", exc_info=True)
