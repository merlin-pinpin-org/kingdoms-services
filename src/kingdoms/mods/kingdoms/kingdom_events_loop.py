"""The weekly event scheduler: the heartbeat behind the cron switches.

``EventService.run_due`` replays every missed weekly slot (cycle switch
Sunday 23:30, age switch Wednesday midnight — Drasah's cadence,
2026-10-11), but nothing called it: the crons sat in the configuration
while no switch ever ran. This module owns the background loop that
ticks on the live bot: every few minutes it replays the due slots
(idempotent — the season state consumes the slots) and announces the
reports in the Géopolitique salon, best effort.

Restart-safe by design: the loop starts once per bot process from the
mod's ``setup_hook`` and the state does the rest — a restart replays
the missed slots on the first tick.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from kingdoms.mods.kingdoms.events import active_lords_day_window
from kingdoms.mods.kingdoms.game_clock import game_now, sync_game_offset
from kingdoms.mods.kingdoms.kingdom_content import announce_geopolitics

logger = logging.getLogger("kingdoms.events_loop")

_TICK_SECONDS = 300

# attack-phases-tick-v1 (Drasah 22/10): the loop wakes AT the next
# attack deadline — the epsilon makes the tick land just past it, the
# floor bounds the churn when a deadline is at the door.
_ATTACK_TICK_EPSILON_SECONDS = 2.0
_ATTACK_TICK_FLOOR_SECONDS = 5.0

_EVENT_SCHEDULER_TASK: asyncio.Task[None] | None = None

_ANNOUNCE_STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "cycle": (
            "🔄 **Cycle {cycle}** — weekly budgets recharged, "
            "{maps} new maps for Gaïa."
        ),
        "age": (
            "🏺 **{age}** — every kingdom gains {tech} tech point(s) "
            "and {marriages} marriage slot(s)."
        ),
        "end": (
            "🏁 **End of the season** — the attacks are cancelled and "
            "the verdict is in: {winner}."
        ),
        "end_showmatch": (
            "🏁 **End of the season** — the attacks are cancelled and "
            "the top kingdoms are tied: the ShowMatch decides!"
        ),
    },
    "fr": {
        "cycle": (
            "🔄 **Cycle {cycle}** — budgets hebdomadaires rechargés, "
            "{maps} nouvelles cartes pour Gaïa."
        ),
        "age": (
            "🏺 **{age}** — chaque royaume gagne {tech} point(s) tech "
            "et {marriages} emplacement(s) de mariage."
        ),
        "end": (
            "🏁 **Fin de saison** — les attaques en cours sont annulées "
            "et le verdict est tombé : {winner}."
        ),
        "end_showmatch": (
            "🏁 **Fin de saison** — les attaques en cours sont annulées, "
            "les deux royaumes de tête sont à égalité : le ShowMatch "
            "décide du vainqueur !"
        ),
    },
}


_THEFT_STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "alarm": (
            "🐦‍⬛ **CORBEAU ALARM** — {thief} steals **{points} ⚙️** from the bank! "
            "You may CONTEST until <t:{ts}:R> ({minutes}-minute window). "
            "The thief may go for a 2nd ⚙️ — a contest kills the Corbeau and refunds EVERYTHING."
        ),
        "prompt": (
            "🐦‍⬛ The Corbeau steals **{points} ⚙️** from **{target}**! Continue?\n"
            "🛑 STOP = fly home safe with the 1st ⚙️; ➡️ CONTINUE = go for the 2nd "
            "(a contest before <t:{ts}:R> kills the Corbeau and you leave with NOTHING). "
            "No answer = automatic safe return with the 1st ⚙️."
        ),
    },
    "fr": {
        "alarm": (
            "🐦‍⬛ **ALARME CORBEAU** — {thief} vole **{points} ⚙️** dans la banque ! "
            "Vous pouvez CONTESTER jusqu'à <t:{ts}:R> (fenêtre de {minutes} min). "
            "Le voleur peut tenter un 2e ⚙️ — une contestation tue le Corbeau et restitue TOUT."
        ),
        "prompt": (
            "🐦‍⬛ Le Corbeau vole **{points} ⚙️** à **{target}** ! Continuer ?\n"
            "🛑 STOP = repartir sain et sauf avec le 1er ⚙️ ; ➡️ CONTINUER = tenter le 2e "
            "(une contestation avant <t:{ts}:R> tue le Corbeau et vous repartez sans rien). "
            "Sans réponse, il repart automatiquement avec le 1er ⚙️."
        ),
    },
}


def _strings(locale: str) -> dict[str, str]:
    return _ANNOUNCE_STRINGS["fr"] if str(locale).lower().startswith("fr") else _ANNOUNCE_STRINGS["en"]


def _report_text(strings: dict[str, str], report: dict[str, object]) -> str:
    """Render one due report as a salon announcement (best effort)."""
    kind = str(report.get("kind", "cycle"))
    if kind == "age":
        return strings["age"].format(
            age=str(report.get("display_name", report.get("age", "?"))),
            tech=report.get("tech_points", 0),
            marriages=report.get("extra_marriages", 0),
        )
    if kind == "end":
        if report.get("showmatch"):
            return strings["end_showmatch"]
        return strings["end"].format(winner=str(report.get("winner_name") or "?"))
    return strings["cycle"].format(
        cycle=report.get("cycle", "?"),
        maps=len(report.get("gaia_new_maps", []) or []),  # type: ignore[arg-type]
    )


async def _guild_locale(bot: Any, guild_id: str) -> str:
    """Return the guild's locale through the logs service (FR default)."""
    logs = getattr(bot, "logs_service", None)
    if logs is None:
        return "fr"
    try:
        return str(await logs.get_locale(guild_id))
    except Exception:
        return "fr"


async def _announce_due_reports_safe(bot: Any, reports: list[dict[str, object]]) -> None:
    """Post every report in each guild's Géopolitique salon (best effort)."""
    for guild in list(getattr(bot, "guilds", [])):
        locale = await _guild_locale(bot, str(guild.id))
        strings = _strings(locale)
        for report in reports:
            try:
                await announce_geopolitics(guild, locale, _report_text(strings, report))
            except Exception:
                logger.warning("KINGDOMS EVENTS: one report announcement failed", exc_info=True)


def start_kingdoms_event_scheduler(bot: Any, *, tick_seconds: int = _TICK_SECONDS) -> bool:
    """Start the background event loop once per bot process.

    Called from the mod's ``setup_hook`` (an async context): the loop
    is a single :mod:`asyncio` task guarded against double starts —
    reconnects and repeated hooks never spawn a second heartbeat.
    """
    global _EVENT_SCHEDULER_TASK
    if _EVENT_SCHEDULER_TASK is not None and not _EVENT_SCHEDULER_TASK.done():
        return True
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.info("KINGDOMS EVENTS: no running loop — the scheduler is not started")
        return False
    _EVENT_SCHEDULER_TASK = loop.create_task(_kingdoms_events_loop(bot, tick_seconds))
    logger.info("KINGDOMS EVENTS: scheduler started (tick=%ss)", tick_seconds)
    return True


async def stop_kingdoms_event_scheduler() -> None:
    """Cancel the heartbeat (tests and clean shutdowns)."""
    global _EVENT_SCHEDULER_TASK
    if _EVENT_SCHEDULER_TASK is not None:
        _EVENT_SCHEDULER_TASK.cancel()
        try:
            await _EVENT_SCHEDULER_TASK
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.info("KINGDOMS EVENTS: scheduler stopped after an error", exc_info=True)
        _EVENT_SCHEDULER_TASK = None


def _auto_snapshotter(time_service: Any) -> Callable[[str], Awaitable[None]] | None:
    """Build the per-slot snapshot hook when the time service is wired."""
    if time_service is None:
        return None

    async def _snapshot(kind: str) -> None:
        await time_service.create_snapshot(f"avant {kind}", moment=kind)

    return _snapshot


async def _season_pause_state(bot: Any) -> str | None:
    """Return why the game clock must NOT advance now, None otherwise.

    Drasah's rules (2026-10-11): a FROZEN season replays no slot (the
    time machine's pause), and a season still in its ``setup`` phase
    replays no slot either — no random content exists before the game
    starts, so a cycle switch must not draw Gaia maps into a setup.
    """
    kingdoms = getattr(bot, "kingdoms_service", None)
    if kingdoms is None:
        return None
    try:
        season = await kingdoms.current_season()
    except Exception:
        logger.warning("KINGDOMS EVENTS: season read failed", exc_info=True)
        return None
    if season is None:
        return None
    if season.frozen:
        return "frozen"
    if season.phase == "setup":
        return "setup"
    return None


async def _snapshot_lords_day_closure_safe(bot: Any, tick_seconds: int) -> None:
    """Snapshot the exact moment the Lord's Day window closes.

    Drasah's rewind rule (2026-10-11): the window that opens at the
    Sunday 23:30 cycle switch closes Monday 10:00 — the state AT that
    exact moment is snapshot (``moment: lords_day_end``) so the
    "return to the Lord's Day end" button lands there.
    """
    kingdoms = getattr(bot, "kingdoms_service", None)
    time_service = getattr(bot, "kingdoms_time_service", None)
    if kingdoms is None or time_service is None:
        return
    try:
        season = await kingdoms.current_season()
        if season is None:
            return
        now = game_now()
        window_now = active_lords_day_window(kingdoms.config, season, now)
        window_before = active_lords_day_window(
            kingdoms.config, season, now - timedelta(seconds=tick_seconds)
        )
        if window_before is not None and window_now is None:
            await time_service.create_snapshot("fin jour du seigneur", moment="lords_day_end")
            logger.info("KINGDOMS EVENTS: Lord's Day window closed — snapshot saved")
    except Exception:
        logger.warning("KINGDOMS EVENTS: Lord's Day closure snapshot failed", exc_info=True)


async def _sync_marriage_locks_safe(bot: Any) -> None:
    """Reconcile the 💍 Verrouillé roles of every guild (every tick).

    Drasah's lock livery (2026-10-11): a fresh spouse wears a RED role
    until the lock expires. This pass IS the expiry engine — the role
    leaves when the timestamp passes, the salon views re-render in
    place (an edit never pings), and a restart mid-honeymoon never
    leaves a ghost red pseudo. It runs even on a paused season: the
    marriage lock is REAL time, not game time.
    """
    from kingdoms.mods.kingdoms.kingdom_persistent import (
        _refresh_realm_views_for_kingdom_safe,
        sync_marriage_lock_roles_safe,
    )

    for guild in list(getattr(bot, "guilds", []) or []):
        try:
            changed = await sync_marriage_lock_roles_safe(guild)
        except Exception:
            logger.warning("KINGDOMS EVENTS: marriage lock sync failed", exc_info=True)
            continue
        for kingdom_id in sorted(changed):
            if kingdom_id:
                try:
                    await _refresh_realm_views_for_kingdom_safe(guild, kingdom_id)
                except Exception:
                    logger.warning(
                        "KINGDOMS EVENTS: post-lock view refresh failed", exc_info=True
                    )


async def _resolve_explorations_safe(bot: Any) -> None:
    """Resolve the due Explorateur locks (marche-v2 art. 6).

    A 48h exploration ends with a fresh map drawn for the kingdom (or a
    refund when the pool ran dry). The tick IS the resolution engine:
    after it, the salon views re-render and the marriage-lock pass (red
    pseudo + 🔒) drops the livery on its next pass.
    """
    from kingdoms.mods.kingdoms.kingdom_persistent import (
        _refresh_realm_views_for_kingdom_safe,
        _wiring,
    )

    for guild in list(getattr(bot, "guilds", []) or []):
        try:
            wiring = _wiring()
            resolved = await wiring.economy_service.resolve_due_explorations()
        except Exception:
            logger.warning("KINGDOMS EVENTS: exploration resolution failed", exc_info=True)
            continue
        kingdoms = {lord.kingdom_id for lord, _ in resolved if lord.kingdom_id}
        for kingdom_id in sorted(kingdoms):
            try:
                await _refresh_realm_views_for_kingdom_safe(guild, kingdom_id)
            except Exception:
                logger.warning(
                    "KINGDOMS EVENTS: post-exploration view refresh failed", exc_info=True
                )


async def _ffa_salon(guild: Any, ffa: Any) -> Any:
    """Return the FFA's ephemeral salon (restructuration-serveur-v1 c).

    The ref rides ``ffa.salon_id`` — never a name lookup, exactly like
    the per-attack salons (restructuration-serveur-v1 b).
    """
    if guild is None:
        return None
    channel_id = int(getattr(ffa, "salon_id", 0) or 0)
    if not channel_id:
        return None
    return getattr(guild, "get_channel", lambda _id: None)(channel_id)


async def _open_ffa_salon_safe(
    guild: Any, ffa_service: Any, ffa: Any, strings: dict[str, Any], text: str, view: Any
) -> None:
    """Open the FFA's ephemeral salon and pin its base message (c).

    restructuration-serveur-v1 c (Drasah 19/10): when a free-for-all
    manifests, a dedicated salon — same logic as the per-attack salons —
    opens right under « Déclarer une attaque » so the sidebar shows what
    is being played. Visible to everyone, @everyone denied send (⚠️
    lesson Check 75: the overwrite set carries an explicit guild.me
    entry). The base message repeats the announcement with the
    persistent « Aller explorer » button; the salon dies with the FFA.
    """
    import discord

    from kingdoms.mods.kingdoms.kingdom_attack_panel import _declare_channel
    from kingdoms.mods.kingdoms.kingdom_setup import _slug

    declare = _declare_channel(guild)
    if declare is None:
        return
    title = str(strings["ffa_salon_title"])
    wanted = _slug(title)
    # A leftover salon from a previous Saturday (or a wipe mid-week) is
    # purged first — the name is fixed, so at most one lives at a time.
    for channel in list(getattr(guild, "text_channels", []) or []):
        if _slug(getattr(channel, "name", "")) == wanted:
            try:
                await channel.delete()
            except Exception:
                logger.warning("KINGDOMS EVENTS: leftover FFA salon purge failed", exc_info=True)
    overwrites: dict[Any, Any] = {}
    everyone = getattr(guild, "default_role", None)
    if everyone is not None:
        overwrites[everyone] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=False,
            read_message_history=True,
        )
    me = getattr(guild, "me", None)
    if me is not None:
        overwrites[me] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_messages=True,
        )
    position = int(getattr(declare, "position", 0) or 0) + 1
    try:
        channel = await guild.create_text_channel(
            title,
            category=getattr(declare, "category", None),
            position=position,
            overwrites=overwrites,
        )
    except Exception:
        logger.warning("KINGDOMS EVENTS: FFA salon creation failed", exc_info=True)
        return
    try:
        await ffa_service.record_salon(ffa, int(channel.id))
    except Exception:
        logger.warning("KINGDOMS EVENTS: FFA salon ref not stored", exc_info=True)
    try:
        await channel.send(text, view=view)
    except Exception:
        logger.warning("KINGDOMS EVENTS: FFA salon base message failed", exc_info=True)


async def _ffa_salon_purge_safe(guild: Any, ffa: Any) -> None:
    """Delete the FFA's ephemeral salon once the FFA is over (c)."""
    channel = await _ffa_salon(guild, ffa)
    if channel is None:
        return
    try:
        await channel.delete()
        logger.info("KINGDOMS EVENTS: FFA salon deleted (FFA over)")
    except Exception:
        logger.warning("KINGDOMS EVENTS: FFA salon deletion failed", exc_info=True)


async def _ffa_announce_safe(bot: Any, ffa_service: Any, ffa: Any) -> None:
    """Announce the drawn FFA in each guild with the join button."""
    import discord

    from kingdoms.mods.kingdoms.kingdom_panels import _strings as panels_strings
    from kingdoms.mods.kingdoms.kingdom_persistent import KingdomFFAJoinButton

    events_settings = ffa_service._config.events
    tz = _ffa_timezone(events_settings)
    maximum = ffa_service.max_participants()
    mode_key = "ffa_mode_one" if ffa.one_lord_per_kingdom else "ffa_mode_free"
    for guild in list(getattr(bot, "guilds", []) or []):
        strings = panels_strings(await _guild_locale(bot, str(guild.id)))
        text = str(strings["ffa_announce"]).format(
            time=ffa.start_at.astimezone(tz).strftime("%Hh%M"),
            duration=int(events_settings.ffa_game_minutes),
            max=maximum,
            mode=strings[mode_key],
        )
        view = discord.ui.View(timeout=None)
        view.add_item(KingdomFFAJoinButton(strings))
        try:
            await announce_geopolitics(guild, "", text, view=view)
        except Exception:
            logger.warning("KINGDOMS EVENTS: FFA announcement failed", exc_info=True)
        # restructuration-serveur-v1 c (Drasah 19/10): the operational
        # surface of the FFA also lives in its OWN ephemeral salon.
        try:
            await _open_ffa_salon_safe(guild, ffa_service, ffa, strings, text, view)
        except Exception:
            logger.warning("KINGDOMS EVENTS: FFA salon open failed", exc_info=True)


async def _ffa_start_safe(bot: Any, ffa_service: Any, started: Any) -> None:
    """Announce the FFA start in each guild (best effort)."""
    from kingdoms.mods.kingdoms.kingdom_panels import _strings as panels_strings

    events_settings = ffa_service._config.events
    for guild in list(getattr(bot, "guilds", []) or []):
        strings = panels_strings(await _guild_locale(bot, str(guild.id)))
        text = str(strings["ffa_start"]).format(
            count=len(started.participants),
            duration=int(events_settings.ffa_game_minutes),
        )
        try:
            await announce_geopolitics(guild, "", text)
        except Exception:
            logger.warning("KINGDOMS EVENTS: FFA start announcement failed", exc_info=True)
        # restructuration-serveur-v1 c: the start line also lands in the
        # FFA's own salon (best effort — the salon may have died).
        salon = await _ffa_salon(guild, started)
        if salon is not None:
            try:
                await salon.send(text)
            except Exception:
                logger.warning("KINGDOMS EVENTS: FFA salon start message failed", exc_info=True)


def _ffa_timezone(events_settings: Any) -> Any:
    """Resolve the scheduling timezone, UTC as the safe fallback."""
    from zoneinfo import ZoneInfo

    try:
        return ZoneInfo(str(getattr(events_settings, "schedule_timezone", "UTC")))
    except Exception:
        return ZoneInfo("UTC")


async def _purge_finished_ffa_salons_safe(bot: Any, ffa: Any) -> None:
    """Delete the FFA's salon in every guild once the FFA is over (c).

    restructuration-serveur-v1 c: the purge is idempotent — a dead ref
    is a no-op — and best effort per guild.
    """
    for guild in list(getattr(bot, "guilds", []) or []):
        try:
            await _ffa_salon_purge_safe(guild, ffa)
        except Exception:
            logger.warning("KINGDOMS EVENTS: FFA salon purge failed", exc_info=True)


async def _ffa_draw_safe(bot: Any, ffa_service: Any, now: Any) -> Any:
    """Read (or draw) this Saturday's FFA document, None when disabled.

    restructuration-serveur-v1 c (Drasah 19/10): extracted from the tick
    so the tick stays under the complexity ceiling — the guards (service
    present, ffa enabled, guilds, live draw) live here.
    """
    events_settings = getattr(getattr(ffa_service, "_config", None), "events", None)
    if events_settings is None or not getattr(events_settings, "ffa_enabled", False):
        return None
    if not list(getattr(bot, "guilds", []) or []):
        return None
    try:
        return await ffa_service.draw_or_get(now)
    except Exception:
        logger.warning("KINGDOMS EVENTS: FFA draw failed", exc_info=True)
        return None


async def _ffa_exploration_tick_safe(bot: Any) -> None:
    """Run the Saturday FFA Exploration tick (ffa-exploration-v1, Drasah 14/10).

    Draw (or read back) this Saturday's random start hour; announce in
    Géopolitique 2h before — the announcement carries the persistent
    « Aller explorer » button and opens the enrollments; at the drawn
    hour the game starts. Every step replays safely through the
    persisted document (announced/started flags).
    """
    ffa_service = getattr(bot, "kingdoms_ffa_service", None)
    if ffa_service is None:
        return
    now = game_now()
    ffa = await _ffa_draw_safe(bot, ffa_service, now)
    if ffa is None:
        return
    # restructuration-serveur-v1 c: a finished FFA takes its ephemeral
    # salon with it — the purge is idempotent (a dead ref is a no-op).
    if getattr(ffa, "finished", False) and getattr(ffa, "salon_id", None):
        await _purge_finished_ffa_salons_safe(bot, ffa)
    try:
        if ffa_service.due_announce(ffa, now):
            await _ffa_announce_safe(bot, ffa_service, ffa)
            await ffa_service.mark_announced(ffa)
            logger.info(
                "KINGDOMS EVENTS: FFA Exploration of %s announced (start %s)",
                ffa.date,
                ffa.start_at.isoformat(),
            )
            return
        if ffa_service.due_start(ffa, now):
            started = await ffa_service.start_due(now)
            if started is None:
                return
            await _ffa_start_safe(bot, ffa_service, started)
            logger.info("KINGDOMS EVENTS: FFA Exploration of %s started", ffa.date)
    except Exception:
        logger.warning("KINGDOMS EVENTS: FFA Exploration tick failed", exc_info=True)


async def _resolve_cathedrals_safe(bot: Any) -> None:
    """Turn every expired chantier into a SACRED territory (lieu-sacree-v1).

    The tick replays the 72h verdicts (idempotent through the
    territory flags) and announces each sacralization in
    Géopolitique — « ampleur évangélique » — while every per-attack
    message stays out (zéro spam, Drasah 14/10).
    """
    cathedral_service = getattr(bot, "kingdoms_cathedral_service", None)
    if cathedral_service is None:
        return
    if not list(getattr(bot, "guilds", []) or []):
        return
    try:
        consecrated = await cathedral_service.resolve_due()
    except Exception:
        logger.warning("KINGDOMS EVENTS: cathedral resolve failed", exc_info=True)
        return
    if not consecrated:
        return
    from kingdoms.mods.kingdoms.kingdom_panels import _strings as panels_strings

    for territory in consecrated:
        kingdom = None
        try:
            kingdoms_service = getattr(bot, "kingdoms_service", None)
            if kingdoms_service is not None:
                kingdom = next(
                    (
                        item
                        for item in await kingdoms_service.kingdoms()
                        if item.id == territory.owner_kingdom_id
                    ),
                    None,
                )
        except Exception:
            logger.warning("KINGDOMS EVENTS: cathedral kingdom lookup failed", exc_info=True)
        name = getattr(kingdom, "name", None) or territory.owner_kingdom_id
        for guild in list(getattr(bot, "guilds", []) or []):
            strings = panels_strings(await _guild_locale(bot, str(guild.id)))
            text = str(strings["cathedral_sacred"]).format(name, territory.map_key)
            try:
                await announce_geopolitics(guild, "", text)
            except Exception:
                logger.warning("KINGDOMS EVENTS: sacred announcement failed", exc_info=True)


def _theft_strings(locale: str) -> dict[str, str]:
    return _THEFT_STRINGS["fr"] if str(locale).lower().startswith("fr") else _THEFT_STRINGS["en"]


async def _realm_realm_channel_safe(guild: Any, kingdom_name: str) -> Any:
    """Find a kingdom's Le-Royaume salon (best effort, read-only)."""
    from kingdoms.mods.kingdoms.kingdom_realms import _find_realm_category
    from kingdoms.mods.kingdoms.kingdom_setup import _slug

    category = _find_realm_category(guild, kingdom_name)
    if category is None:
        return None
    return next(
        (
            channel
            for channel in getattr(category, "channels", [])
            if _slug(getattr(channel, "name", "")) == _slug("Le-Royaume")
        ),
        None,
    )


async def _resolve_corbeau_thefts_safe(bot: Any) -> None:
    """Execute the due corbeau ⚙️ thefts and ring the alarms (corbeau-v3).

    The tick IS the theft engine (idempotent through the theft states):
    a pending theft past its 1-hour ``execute_at`` steals the 1st ⚙️,
    opens the 5-minute contest window, sends the ALARM (persistent 🛡️
    Contester button) to the robbed kingdom's Le-Royaume salon and the
    « Continuer ? » prompt (➡️/🛑 persistent buttons) to the thief's.
    A bank at 0 settles silently. Best effort on every Discord step.
    """
    voliere = getattr(bot, "kingdoms_voliere_service", None)
    kingdoms_service = getattr(bot, "kingdoms_service", None)
    if voliere is None or kingdoms_service is None:
        return
    if not list(getattr(bot, "guilds", []) or []):
        return
    try:
        executed = await voliere.resolve_due_thefts(now=game_now())
    except Exception:
        logger.warning("KINGDOMS EVENTS: corbeau theft resolution failed", exc_info=True)
        return
    if not executed:
        return
    try:
        kingdoms = {str(k.id): k for k in await kingdoms_service.kingdoms()}
    except Exception:
        logger.warning("KINGDOMS EVENTS: theft kingdoms lookup failed", exc_info=True)
        return
    minutes = int(
        getattr(getattr(voliere, "settings", None), "corbeau_theft_contest_minutes", 5)
    )
    for theft in executed:
        await _ring_theft_alarms_safe(bot, theft, kingdoms, minutes)
        logger.info(
            "KINGDOMS EVENTS: corbeau theft %s executed (%s ⚙️ from %s)",
            theft.get("id"),
            theft.get("stolen", 0),
            theft.get("target_kingdom_name"),
        )


async def _ring_theft_alarms_safe(
    bot: Any, theft: dict[str, Any], kingdoms: dict[str, Any], minutes: int
) -> None:
    """Send the ALARM (robbed side) and the prompt (thief side), best effort."""
    from kingdoms.mods.kingdoms.kingdom_voliere_panel import (
        theft_alarm_view,
        theft_prompt_view,
    )

    theft_id = str(theft.get("id"))
    target_name = str(theft.get("target_kingdom_name") or "")
    thief_name = str(
        getattr(kingdoms.get(str(theft.get("thief_kingdom_id"))), "name", "")
        or str(theft.get("thief_kingdom_id"))
    )
    points = int(theft.get("stolen", 0) or 0)
    for guild in list(getattr(bot, "guilds", []) or []):
        locale = await _guild_locale(bot, str(guild.id))
        strings = _theft_strings(locale)
        try:
            # ALARM — the robbed kingdom's Le-Royaume salon.
            target_channel = await _realm_realm_channel_safe(guild, target_name)
            if target_channel is not None:
                await target_channel.send(
                    strings["alarm"].format(
                        thief=thief_name,
                        points=points,
                        ts=int(theft.get("contest_until", 0) or 0),
                        minutes=minutes,
                    ),
                    view=theft_alarm_view(locale),
                )
        except Exception:
            logger.warning("KINGDOMS EVENTS: theft alarm failed", exc_info=True)
        try:
            # PROMPT — the thief kingdom's Le-Royaume salon.
            thief_channel = await _realm_realm_channel_safe(guild, thief_name)
            if thief_channel is not None:
                await thief_channel.send(
                    strings["prompt"].format(
                        points=points,
                        target=target_name,
                        ts=int(theft.get("contest_until", 0) or 0),
                    ),
                    view=theft_prompt_view(locale, theft_id),
                )
        except Exception:
            logger.warning("KINGDOMS EVENTS: theft prompt failed", exc_info=True)


async def _mercenary_tick_safe(bot: Any) -> None:
    """Run the Chef Mercenaire tick (chef-mercenaire-v1, Drasah 24/10).

    The Chief's clock is REAL Paris wall time (never the offset game
    clock): inside the 20h-21h window the daily market opens
    (idempotent — a market already drawn is never re-drawn, a CLOSED
    market of TODAY stays displayed until 21h, a closed market of
    ANOTHER day lets a fresh one open) and every tick closes the due
    market (replay-safe awards/refunds). The opening is skipped on a
    paused (frozen/setup) season, but a live market still closes —
    the escrowed écus must always come home. Best effort throughout.
    """
    from datetime import UTC, datetime
    from zoneinfo import ZoneInfo

    from kingdoms.mods.kingdoms.mercenary import MercenaryError

    mercenary = getattr(bot, "kingdoms_mercenary_service", None)
    if mercenary is None:
        return
    moment = datetime.now(UTC)
    paris_today = moment.astimezone(ZoneInfo("Europe/Paris")).strftime("%Y-%m-%d")
    doc = None
    try:
        doc = await mercenary.market()
    except Exception:
        logger.warning("KINGDOMS EVENTS: mercenary market read failed", exc_info=True)
    opens = False
    if doc is None:
        opens = True
    elif doc.get("closed", False):
        opens = str(doc.get("date")) != paris_today
    if opens and mercenary.inside_window(moment):
        try:
            kingdoms = getattr(bot, "kingdoms_service", None)
            season = None
            if kingdoms is not None:
                season = await kingdoms.current_season()
            if season is None or (not season.frozen and season.phase != "setup"):
                await mercenary.open_market(now=moment)
                logger.info("KINGDOMS EVENTS: mercenary market opened (window)")
        except MercenaryError:
            pass  # the window guard raced us — the next tick retries
        except Exception:
            logger.warning("KINGDOMS EVENTS: mercenary open failed", exc_info=True)
    try:
        awards = await mercenary.close_if_due(now=moment)
    except Exception:
        logger.warning("KINGDOMS EVENTS: mercenary close failed", exc_info=True)
        return
    if awards:
        logger.info(
            "KINGDOMS EVENTS: mercenary market closed — %s award(s)", len(awards)
        )


async def _beta_tick_seconds(bot: Any, default_seconds: int) -> int:
    """Return the loop sleep for this iteration: 60s under an armed beta.

    Drasah 18/10: the beta windows are minutes (defense cutoff T-5,
    draft at the reveal) while the stock tick is 300s — the reveal
    and the draft DMs must land within the minute, not within 5.
    Best effort: any failure keeps the stock cadence.
    """
    try:
        from kingdoms.mods.kingdoms.beta_weekend import beta_started

        service = getattr(bot, "kingdoms_service", None)
        if service is None:
            return default_seconds
        season = await service.current_season()
        if season is not None and beta_started(season, game_now()):
            return min(default_seconds, 60)
    except Exception:
        logger.warning("KINGDOMS EVENTS: beta tick probe failed", exc_info=True)
    return default_seconds


async def _loop_sleep_seconds(bot: Any, default_seconds: int) -> int:
    """Return the loop sleep for this iteration (attack-phases-tick-v1).

    Drasah 22/10: « fais ce qu'il faut pour respecter les timers » —
    the stock heartbeat (300 s, 60 s under an armed beta) let every
    attack phase land up to 5 minutes late: the T-15 reveal, the
    meeting-time AI flip, the attacker's draft DM, the AI's 30-90 s
    draft/Prêt « think time » and the vocal opening each waited for the
    next tick. The loop now asks the attacks service for the NEAREST
    upcoming deadline and sleeps exactly that long (plus an epsilon, so
    the tick lands just past the deadline) — bounded by the stock
    cadence: an idle board sleeps as long as before, a deadline at the
    door wakes the loop within seconds. Best effort: any probe failure
    keeps the stock (or beta) cadence.
    """
    base = await _beta_tick_seconds(bot, default_seconds)
    attacks_service = getattr(bot, "kingdoms_attacks_service", None)
    if attacks_service is not None:
        try:
            now = game_now()
            deadline = await attacks_service.next_phase_deadline(now=now)
            if deadline is not None:
                delta = (deadline - now).total_seconds()
                if delta <= 0:
                    # A deadline already due: run the tick's work now.
                    return 1
                seconds = max(_ATTACK_TICK_FLOOR_SECONDS, delta + _ATTACK_TICK_EPSILON_SECONDS)
                return min(base, int(seconds))
        except Exception:
            logger.warning("KINGDOMS EVENTS: attack deadline probe failed", exc_info=True)
    # chef-mercenaire-v1: the auction close is a REAL-time deadline —
    # the loop wakes at the (anti-snipe extended) close so the awards
    # land on the second, never a whole tick late. Best effort.
    mercenary_service = getattr(bot, "kingdoms_mercenary_service", None)
    if mercenary_service is not None:
        try:
            from datetime import UTC, datetime

            doc = await mercenary_service.market()
            if doc and not doc.get("closed", False):
                delta = int(doc["closes_at"]) - int(datetime.now(UTC).timestamp())
                if delta <= 0:
                    return 1
                seconds = max(_ATTACK_TICK_FLOOR_SECONDS, delta + _ATTACK_TICK_EPSILON_SECONDS)
                return min(base, int(seconds))
        except Exception:
            logger.warning("KINGDOMS EVENTS: mercenary deadline probe failed", exc_info=True)
    return base


async def _kingdoms_events_loop(bot: Any, tick_seconds: int) -> None:
    """Tick forever: replay the due weekly slots, then announce."""
    while True:
        await asyncio.sleep(await _loop_sleep_seconds(bot, tick_seconds))
        # machine-temps-v2: re-sync the game-clock offset cache from
        # the persisted season every tick — a restarted (or wiped)
        # process converges back to the test offset within one tick.
        await sync_game_offset(getattr(bot, "kingdoms_service", None))
        await _sync_marriage_locks_safe(bot)
        await _mercenary_tick_safe(bot)
        events = getattr(bot, "kingdoms_events_service", None)
        if events is None:
            continue
        pause_reason = await _season_pause_state(bot)
        if pause_reason is not None:
            continue
        snapshotter = _auto_snapshotter(getattr(bot, "kingdoms_time_service", None))
        try:
            reports = await events.run_due(game_now(), snapshotter=snapshotter)
        except Exception:
            logger.warning("KINGDOMS EVENTS: due-slot replay failed", exc_info=True)
            continue
        await _snapshot_lords_day_closure_safe(bot, tick_seconds)
        # D77 attack phases: T-15 reveal, T-5 pick & ban DMs, T forfeits —
        # every step replays safely through the service-side flags.
        from kingdoms.mods.kingdoms.kingdom_attack_panel import run_attack_phases_safe

        try:
            await run_attack_phases_safe(bot)
        except Exception:
            logger.warning("KINGDOMS EVENTS: attack phases failed", exc_info=True)
        await _resolve_explorations_safe(bot)
        await _ffa_exploration_tick_safe(bot)
        await _resolve_cathedrals_safe(bot)
        await _resolve_corbeau_thefts_safe(bot)
        if not reports:
            continue
        logger.info("KINGDOMS EVENTS: %s due slot(s) replayed", len(reports))
        try:
            await _announce_due_reports_safe(bot, reports)
        except Exception:
            logger.warning("KINGDOMS EVENTS: announcement failed", exc_info=True)
        # a replayed slot moves wallets, territories and budgets with no
        # click — re-render every salon state view so nothing goes stale
        try:
            await _refresh_views_after_due_safe(bot)
        except Exception:
            logger.warning("KINGDOMS EVENTS: view refresh failed", exc_info=True)


async def _refresh_views_after_due_safe(bot: Any) -> None:
    """Re-render the state views of each guild after the scheduler ran."""
    from kingdoms.mods.kingdoms.kingdom_persistent import _refresh_all_realm_views_safe

    for guild in list(getattr(bot, "guilds", [])):
        locale = await _guild_locale(bot, str(guild.id))
        await _refresh_all_realm_views_safe(guild, locale)