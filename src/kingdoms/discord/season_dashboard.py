"""The Gestion-saison dashboard and the Temps de saison timer (epic #214, phase 1.1).

Two pinned messages, both following the marker contract of
``refresh_season_status`` (one message per channel, identified by an
inline marker, edited in place on every refresh — never duplicated):

- **gestion-saison** (Admin category): the admin season dashboard —
  cycle N/total, Jour J (the next weekly switch), current age, live
  headcount (kingdoms / active lords / waiting queue), pause status and
  the season's active time counter.
- **Temps de saison** (Époque category): the public live timers as
  Discord ``<t:…>`` tags — season start, next Jour J and season end.
  Read-only for everyone (overwrites enforced at bootstrap, like the
  epoch channel).

Both read the season through ``KingdomsService.current_season()``
(the §3b state-reconstruction contract: no captured state); the pause
flag is read defensively (``getattr``) because pause/reprise lands in
phase 1.2 — until then a season always displays "active".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import discord

logger = logging.getLogger("kingdoms.season_dashboard")

__all__ = [
    "SEASON_DASHBOARD_CHANNEL",
    "SEASON_DASHBOARD_MARKER",
    "SEASON_TIME_CHANNEL",
    "SEASON_TIME_MARKER",
    "SeasonSnapshot",
    "build_dashboard_content",
    "build_time_content",
    "next_cycle_at",
    "refresh_season_dashboard",
    "refresh_season_time",
    "season_end_at",
    "snapshot_from_services",
]

SEASON_DASHBOARD_CHANNEL = "gestion-saison"
SEASON_DASHBOARD_MARKER = "kingdoms:season:dashboard"
SEASON_TIME_CHANNEL = "temps-de-saison"
SEASON_TIME_MARKER = "kingdoms:season:time"

WEEK = timedelta(weeks=1)

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "dashboard_title": "🧭 Kingdoms — season management",
        "dashboard_no_season": "No season is running — launch one from the Paramètres panel.",
        "dashboard_season": "Season",
        "dashboard_cycle": "Cycle",
        "dashboard_jour_j": "Jour J (next cycle)",
        "dashboard_age": "Age",
        "dashboard_pause": "Season status",
        "dashboard_pause_active": "🟢 active",
        "dashboard_pause_paused": "🟠 paused",
        "dashboard_active_time": "Active since",
        "dashboard_headcount": "Headcount",
        "dashboard_kingdoms": "kingdoms",
        "dashboard_lords": "active lords",
        "dashboard_queued": "waiting",
        "dashboard_hint": "This dashboard is refreshed by the bot (deploy, launch, status).",
        "time_title": "⏳ Season time",
        "time_no_season": "No season is running.",
        "time_started": "Season start",
        "time_jour_j": "Next Jour J",
        "time_ends": "Season end",
        "time_hint": "The timers below update live — nothing to do here.",
    },
    "fr": {
        "dashboard_title": "🧭 Kingdoms — gestion de la saison",
        "dashboard_no_season": "Aucune saison en cours — lancez-en une depuis le panneau Paramètres.",
        "dashboard_season": "Saison",
        "dashboard_cycle": "Cycle",
        "dashboard_jour_j": "Jour J (prochain cycle)",
        "dashboard_age": "Âge",
        "dashboard_pause": "Statut saison",
        "dashboard_pause_active": "🟢 active",
        "dashboard_pause_paused": "🟠 en pause",
        "dashboard_active_time": "Active depuis",
        "dashboard_headcount": "Effectifs",
        "dashboard_kingdoms": "royaumes",
        "dashboard_lords": "seigneurs actifs",
        "dashboard_queued": "en attente",
        "dashboard_hint": "Ce tableau de bord est mis à jour par le bot (déploiement, lancement, statut).",
        "time_title": "⏳ Temps de saison",
        "time_no_season": "Aucune saison en cours.",
        "time_started": "Début de la saison",
        "time_jour_j": "Prochain Jour J",
        "time_ends": "Fin de la saison",
        "time_hint": "Les compteurs ci-dessous se mettent à jour en direct — rien à faire ici.",
    },
}


def _strings(locale: str) -> dict[str, str]:
    return STRINGS["fr"] if str(locale).lower().startswith("fr") else STRINGS["en"]


def _as_utc(moment: datetime) -> datetime:
    """Coerce a datetime to UTC (naive reads as UTC)."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def discord_timestamp(moment: datetime, style: str = "F") -> str:
    """Render a datetime as a live Discord ``<t:…:style>`` tag."""
    return f"<t:{int(_as_utc(moment).timestamp())}:{style}>"


def next_cycle_at(started_at: datetime, current_cycle: int) -> datetime:
    """Compute the next Jour J: one cycle per week since the launch (D1)."""
    return _as_utc(started_at) + WEEK * (current_cycle + 1)


def season_end_at(started_at: datetime, weeks: int) -> datetime:
    """Compute the season's end: ``weeks`` weekly cycles after the launch (D55)."""
    return _as_utc(started_at) + WEEK * weeks


@dataclass(frozen=True, slots=True)
class SeasonSnapshot:
    """Everything the dashboard and the timers display (pure data)."""

    season_id: str
    cycle: int
    total_cycles: int
    age_name: str
    started_at: datetime
    jour_j: datetime
    ends_at: datetime
    paused: bool
    kingdoms: int
    active_lords: int
    queued: int


async def snapshot_from_services(kingdoms_service: Any) -> SeasonSnapshot | None:
    """Read the current season through the service; None when none runs."""
    if kingdoms_service is None:
        return None
    season = await kingdoms_service.current_season()
    if season is None:
        return None
    config = kingdoms_service.config
    age_name = next(
        (age.display_name for age in config.ages if age.key == season.current_age_key),
        season.current_age_key,
    )
    kingdoms = await kingdoms_service.kingdoms()
    lords = await kingdoms_service.lords()
    return SeasonSnapshot(
        season_id=season.id,
        cycle=season.current_cycle + 1,
        total_cycles=season.weeks,
        age_name=age_name,
        started_at=_as_utc(season.started_at),
        jour_j=next_cycle_at(season.started_at, season.current_cycle),
        ends_at=season_end_at(season.started_at, season.weeks),
        # Phase 1.2 adds the pause flag to the model; absent = active.
        paused=bool(getattr(season, "paused", False)),
        kingdoms=sum(1 for kingdom in kingdoms if not kingdom.is_gaia),
        active_lords=sum(1 for lord in lords if not lord.left and not lord.in_queue),
        queued=sum(1 for lord in lords if not lord.left and lord.in_queue),
    )


def build_dashboard_content(snapshot: SeasonSnapshot | None, locale: str) -> str:
    """Render the gestion-saison dashboard message."""
    strings = _strings(locale)
    if snapshot is None:
        return "\n".join([f"# {strings['dashboard_title']}", strings["dashboard_no_season"], SEASON_DASHBOARD_MARKER])
    lines = [
        f"# {strings['dashboard_title']}",
        f"**{strings['dashboard_season']}** : `{snapshot.season_id}`",
        f"**{strings['dashboard_cycle']}** : {snapshot.cycle}/{snapshot.total_cycles}",
        (
            f"**{strings['dashboard_jour_j']}** : {discord_timestamp(snapshot.jour_j)} "
            f"({discord_timestamp(snapshot.jour_j, 'R')})"
        ),
        f"**{strings['dashboard_age']}** : {snapshot.age_name}",
        (
            f"**{strings['dashboard_pause']}** : "
            f"{strings['dashboard_pause_paused'] if snapshot.paused else strings['dashboard_pause_active']}"
        ),
        (
            f"**{strings['dashboard_active_time']}** : {discord_timestamp(snapshot.started_at)} "
            f"({discord_timestamp(snapshot.started_at, 'R')})"
        ),
        (
            f"**{strings['dashboard_headcount']}** : {snapshot.kingdoms} {strings['dashboard_kingdoms']} · "
            f"{snapshot.active_lords} {strings['dashboard_lords']} · "
            f"{snapshot.queued} {strings['dashboard_queued']}"
        ),
        f"*{strings['dashboard_hint']}*",
        SEASON_DASHBOARD_MARKER,
    ]
    return "\n".join(lines)


def build_time_content(snapshot: SeasonSnapshot | None, locale: str) -> str:
    """Render the Temps de saison live timers message."""
    strings = _strings(locale)
    if snapshot is None:
        return "\n".join([f"# {strings['time_title']}", strings["time_no_season"], SEASON_TIME_MARKER])
    lines = [
        f"# {strings['time_title']}",
        f"**{strings['time_started']}** : {discord_timestamp(snapshot.started_at)}",
        (
            f"**{strings['time_jour_j']}** : {discord_timestamp(snapshot.jour_j)} "
            f"({discord_timestamp(snapshot.jour_j, 'R')})"
        ),
        (
            f"**{strings['time_ends']}** : {discord_timestamp(snapshot.ends_at)} "
            f"({discord_timestamp(snapshot.ends_at, 'R')})"
        ),
        f"*{strings['time_hint']}*",
        SEASON_TIME_MARKER,
    ]
    return "\n".join(lines)


async def _refresh_marker_message(
    channel: discord.abc.Messageable | None,
    content: str,
    marker: str,
) -> bool:
    """Post (or edit in place) the single marker message of a channel."""
    if channel is None:
        return False
    for message in list(getattr(channel, "messages", []) or []):
        if marker in (getattr(message, "content", "") or ""):
            try:
                await message.edit(content=content)
            except Exception:
                logger.warning("SEASON DASHBOARD: refresh of %s failed", marker, exc_info=True)
            return True
    await channel.send(content)
    return True


def _find_channel(guild: discord.Guild, slug: str) -> discord.abc.Messageable | None:
    from kingdoms.discord.kingdom_setup import _slug

    return next((c for c in guild.text_channels if _slug(c.name) == slug), None)


async def refresh_season_dashboard(
    guild: discord.Guild,
    locale: str,
    kingdoms_service: Any = None,
) -> bool:
    """Post (or refresh) the pinned dashboard in gestion-saison."""
    snapshot = await snapshot_from_services(kingdoms_service)
    content = build_dashboard_content(snapshot, locale)
    posted = await _refresh_marker_message(
        _find_channel(guild, SEASON_DASHBOARD_CHANNEL), content, SEASON_DASHBOARD_MARKER
    )
    logger.info(
        "KINGDOMS SEASON DASHBOARD: refreshed (season=%s, cycle=%s)",
        snapshot.season_id if snapshot else "none",
        snapshot.cycle if snapshot else "-",
    )
    return posted


async def refresh_season_time(
    guild: discord.Guild,
    locale: str,
    kingdoms_service: Any = None,
) -> bool:
    """Post (or refresh) the live timers in Temps de saison (once per cycle)."""
    snapshot = await snapshot_from_services(kingdoms_service)
    content = build_time_content(snapshot, locale)
    return await _refresh_marker_message(
        _find_channel(guild, SEASON_TIME_CHANNEL), content, SEASON_TIME_MARKER
    )
