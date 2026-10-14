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
from datetime import UTC, datetime, timedelta
from typing import Any

from kingdoms.mods.kingdoms.events import active_lords_day_window
from kingdoms.mods.kingdoms.kingdom_content import announce_geopolitics

logger = logging.getLogger("kingdoms.events_loop")

_TICK_SECONDS = 300

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
        now = datetime.now(tz=UTC)
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


def _ffa_timezone(events_settings: Any) -> Any:
    """Resolve the scheduling timezone, UTC as the safe fallback."""
    from zoneinfo import ZoneInfo

    try:
        return ZoneInfo(str(getattr(events_settings, "schedule_timezone", "UTC")))
    except Exception:
        return ZoneInfo("UTC")


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
    events_settings = getattr(getattr(ffa_service, "_config", None), "events", None)
    if events_settings is None or not getattr(events_settings, "ffa_enabled", False):
        return
    if not list(getattr(bot, "guilds", []) or []):
        return
    now = datetime.now(tz=UTC)
    try:
        ffa = await ffa_service.draw_or_get(now)
    except Exception:
        logger.warning("KINGDOMS EVENTS: FFA draw failed", exc_info=True)
        return
    if ffa is None:
        return
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


async def _kingdoms_events_loop(bot: Any, tick_seconds: int) -> None:
    """Tick forever: replay the due weekly slots, then announce."""
    while True:
        await asyncio.sleep(tick_seconds)
        await _sync_marriage_locks_safe(bot)
        events = getattr(bot, "kingdoms_events_service", None)
        if events is None:
            continue
        pause_reason = await _season_pause_state(bot)
        if pause_reason is not None:
            continue
        snapshotter = _auto_snapshotter(getattr(bot, "kingdoms_time_service", None))
        try:
            reports = await events.run_due(datetime.now(tz=UTC), snapshotter=snapshotter)
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
