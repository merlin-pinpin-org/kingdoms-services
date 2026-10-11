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
from datetime import UTC, datetime
from typing import Any

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


async def _kingdoms_events_loop(bot: Any, tick_seconds: int) -> None:
    """Tick forever: replay the due weekly slots, then announce."""
    while True:
        await asyncio.sleep(tick_seconds)
        events = getattr(bot, "kingdoms_events_service", None)
        if events is None:
            continue
        try:
            reports = await events.run_due(datetime.now(tz=UTC))
        except Exception:
            logger.warning("KINGDOMS EVENTS: due-slot replay failed", exc_info=True)
            continue
        if not reports:
            continue
        logger.info("KINGDOMS EVENTS: %s due slot(s) replayed", len(reports))
        try:
            await _announce_due_reports_safe(bot, reports)
        except Exception:
            logger.warning("KINGDOMS EVENTS: announcement failed", exc_info=True)
