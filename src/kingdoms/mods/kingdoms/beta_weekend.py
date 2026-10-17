"""Beta weekend presets (beta-weekend-v2, Drasah 17/10/2026).

Drasah opens a multi-user beta test compressed into a 52-hour window
that starts at an EXACT moment he picks: the console command
``lancer_beta now`` opens a Discord interface (buttons + modal) to
define that precise start — T0. From T0 the whole season follows the
relative plan: age switch at T0+4h, two Lord's Day cycles at T0+10h
and T0+34h (2h protected windows), and the official season end at
T0+52h (results + rewards). The compressed config applies AT T0, not
at the arming: a beta armed for Friday 20:00 leaves the season at its
normal pace until Friday 20:00 sharp.

``now`` replaces the v1 preset ``1.0`` (anchored on calendar slots);
``1.0`` stays loadable so a season armed before the v2 deploy never
breaks. A normal season is untouched — nothing changes unless the
console says so.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from kingdoms.mods.kingdoms.config import (
    AttackSettings,
    Epoch,
    EventSettings,
    KingdomsSeasonConfig,
    ParishSettings,
    ParishTier,
    TechnologyCosts,
    VoliereSettings,
)

BETA_TIMEZONE = "Europe/Paris"

# The relative 52h plan (beta-weekend-v2, Drasah 17/10/2026) — every
# offset rides T0, the exact start the console interface captured:
#   age   : T0 + 4h   (Sombre → Féodal, nothing beyond)
#   cycle : T0 + 10h  (Jour du Seigneur, 2h protected window)
#   cycle : T0 + 34h  (second Jour du Seigneur)
#   end   : T0 + 52h  (official end: results + rewards)
BETA_PLAN_OFFSETS: dict[str, Any] = {
    "age_hours": 4,
    "cycle_hours": (10, 34),
    "end_hours": 52,
    "lords_day_window_hours": 2,
}

# Preset « now » (Drasah 17/10/2026) — decisions verbatim: 2 attaques
# / 2 défenses, Sombre → Féodal only, Jour du Seigneur inside the
# 52h plan, special actions free and unlimited, every delay
# compressed to minutes (10-minute attack/Gaïa/Embuscade delays).
BETA_PRESETS: dict[str, dict[str, Any]] = {
    "now": {
        "attacks": {
            "attacks_per_week": 2,
            "defenses_per_week": 2,
            "player_attack_delay_minutes": 10,
            "gaia_attack_delay_minutes": 10,
            "embuscade_delay_minutes": 10,
            "gaia_attack_max_per_kingdom": 2,
            "defense_cutoff_minutes": 5,
            "pick_ban_lead_minutes": 3,
            "ready_grace_minutes": 3,
            "score_reminder_hours": 0.5,
            "score_dispute_after_reminder_hours": 1.0,
        },
        "voliere": {
            "corbeau_civ_cooldown_hours": 4,
            "corbeau_verify_cooldown_hours": 2,
            "corbeau_track_cooldown_hours": 6,
            "corbeau_track_window_hours": 1,
            "faucon_territory_cooldown_hours": 8,
            "faucon_sabotage_cooldown_hours": 4,
            "faucon_release_cooldown_hours": 12,
            "faucon_release_window_hours": 2,
            "pigeon_civ_cooldown_hours": 4,
            "pigeon_question_cooldown_hours": 1,
            "pigeon_dm_cooldown_hours": 1,
        },
        "events": {
            "exploration_cron": "0 12 * * SAT",
            "ffa_announce_lead_hours": 1,
            "ffa_no_show_lock_hours": 6,
            "ffa_start_hours": (14, 15, 16, 17, 18, 19, 20, 21, 22),
        },
        "parish": {
            "tiers": (
                ParishTier(key="chapelle", display_name="Chapelle", cost=0, marriage_lock_hours=4, offering=0),
                ParishTier(key="eglise", display_name="Église", cost=2, marriage_lock_hours=2, offering=1),
                ParishTier(key="cathedrale", display_name="Cathédrale", cost=4, marriage_lock_hours=1, offering=2),
            ),
            "cathedral_chantier_hours": 8,
        },
        "technologies": {
            "embuscade": 0,
            "traquenard": 0,
            "patrouille": 0,
            "contre_espionnage": 0,
            "sabotage": 0,
            "explorateur": 0,
            "jeu_d_armes": 0,
            "mariage_arrange": 0,
            "corruption": 0,
            "garde_royale": 0,
            "limits": {},
        },
        # Two ages only: Sombre → Féodal, nothing beyond.
        "ages": (
            Epoch(key="dark_age", display_name="Âge sombre", gaia_ai_level=2, tech_points=0, extra_marriages=1),
            Epoch(key="feudal_age", display_name="Âge féodal", gaia_ai_level=3, tech_points=1, extra_marriages=1),
        ),
    },
}

# The legacy v1 preset (calendar-anchored) — kept LOADABLE so a
# season armed with ``1.0`` before the v2 deploy re-applies the exact
# values it was armed with. The console only advertises ``now``.
_legacy_1_0 = dict(BETA_PRESETS["now"])
_legacy_delays = {"player_attack_delay_minutes": 60, "gaia_attack_delay_minutes": 60, "embuscade_delay_minutes": 60}
_legacy_1_0["attacks"] = {**_legacy_1_0["attacks"], **_legacy_delays}
BETA_PRESETS["1.0"] = _legacy_1_0


def beta_preset_names() -> tuple[str, ...]:
    """Return the preset names the console advertises (``now`` first)."""
    names = tuple(sorted(BETA_PRESETS, reverse=True))
    return names


def apply_beta_preset(config: KingdomsSeasonConfig, name: str) -> None:
    """Mutate the shared season config in place onto the preset.

    Raises ``ValueError`` on an unknown preset — a typo in the console
    must never silently run a normal-paced beta. Every service holds
    the SAME config object, so the new values apply instantly; the
    scheduler re-applies the preset after a restart from the season
    document.
    """
    preset = BETA_PRESETS.get(name)
    if preset is None:
        raise ValueError(f"preset beta inconnu : {name!r} (disponibles : {', '.join(beta_preset_names())})")
    config.attacks = AttackSettings(**{**config.attacks.model_dump(), **preset["attacks"]})
    config.voliere = VoliereSettings(**{**config.voliere.model_dump(), **preset["voliere"]})
    config.events = EventSettings(**{**config.events.model_dump(), **preset["events"]})
    config.parish = ParishSettings(**{**config.parish.model_dump(), **preset["parish"]})
    config.technologies = TechnologyCosts(**{**config.technologies.model_dump(), **preset["technologies"]})
    config.ages = tuple(preset["ages"])


def beta_timezone() -> ZoneInfo:
    """Return the beta scheduling timezone (Europe/Paris)."""
    try:
        return ZoneInfo(BETA_TIMEZONE)
    except Exception:  # pragma: no cover - the stock tz database has it
        return ZoneInfo("UTC")


# « 23/10 20:00 », « 23/10/2026 20:00 », « maintenant », « now ».
_START_RE = re.compile(r"^(\d{1,2})/(\d{1,2})(?:/(\d{4}))?\s+(\d{1,2}):(\d{2})$")
_NOW_WORDS = {"maintenant", "now"}


def parse_beta_start(text: str, now: datetime) -> datetime:
    """Parse the console/modal start input into a Paris datetime.

    Accepted: ``JJ/MM HH:MM``, ``JJ/MM/AAAA HH:MM``, ``maintenant``.
    A start in the past is REFUSED — the interface defines when the
    beta begins, never rewinds it (idempotent re-arming is done by
    re-running the command with a new date).
    """
    tz = beta_timezone()
    if now.tzinfo is None:
        now = now.replace(tzinfo=ZoneInfo("UTC"))
    cleaned = text.strip().casefold()
    if cleaned in _NOW_WORDS:
        return now.astimezone(tz)
    match = _START_RE.match(cleaned)
    if match is None:
        raise ValueError("format attendu : JJ/MM HH:MM (ex. 23/10 20:00) ou « maintenant »")
    day, month, year, hour, minute = (int(part) for part in match.groups(default="0") if part is not None)
    year = year or now.astimezone(tz).year
    try:
        start = datetime(year, month, day, hour, minute, tzinfo=tz)
    except ValueError as error:
        raise ValueError(f"date invalide : {error}") from error
    if start <= now.astimezone(tz):
        raise ValueError("le départ doit être dans le futur (ex. 23/10 20:00)")
    return start


def plan_beta_slots(name: str, start: datetime) -> dict[str, Any]:
    """Compute the 52h slot plan from the EXACT start ``start`` (T0).

    beta-weekend-v2: the plan rides T0 — age at T0+4h, cycles at
    T0+10h/T0+34h, official end at T0+52h — whatever the wall clock.
    """
    preset = BETA_PRESETS.get(name)
    if preset is None:
        raise ValueError(f"preset beta inconnu : {name!r} (disponibles : {', '.join(beta_preset_names())})")
    del preset  # the plan is preset-independent in v2 (the 52h frame)
    tz = beta_timezone()
    if start.tzinfo is None:
        start = start.replace(tzinfo=ZoneInfo("UTC"))
    start = start.astimezone(tz)
    offsets = BETA_PLAN_OFFSETS
    return {
        "start_at": start,
        "age_at": start + timedelta(hours=offsets["age_hours"]),
        "cycle_slots": [
            start + timedelta(hours=hours) for hours in offsets["cycle_hours"]
        ],
        "end_at": start + timedelta(hours=offsets["end_hours"]),
        "lords_day_window_hours": offsets["lords_day_window_hours"],
    }


def _utc(value: datetime) -> datetime:
    """Return ``value`` as an offset-aware UTC datetime.

    The MongoDB round-trip strips the tzinfo (a BSON datetime reloads
    as plain naive UTC — ``MongoClient`` is not ``tz_aware``), while
    the game clock is always aware: comparing the two raises
    « can't compare offset-naive and offset-aware datetimes ». A naive
    value is read as UTC (the wire epoch IS UTC), an aware one is
    converted.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=ZoneInfo("UTC"))
    return value.astimezone(ZoneInfo("UTC"))


def beta_lords_day_window(
    season: Any,
    now: datetime,
) -> tuple[datetime, datetime] | None:
    """Return the beta Lord's Day window around the last cycle slot.

    Each beta cycle slot opens a short protected window (2h) where no
    attack is PLAYED — the compressed equivalent of the Sunday 23:30
    → Monday 10:00 season window. Returns None outside the windows
    (and on an unknown preset name — never block a season on a
    missing preset).
    """
    if not BETA_PRESETS.get(str(getattr(season, "beta_name", None) or "")):
        return None
    now = _utc(now)
    for raw in list(getattr(season, "beta_cycle_slots", None) or []):
        slot = _utc(raw)
        end = slot + timedelta(hours=int(BETA_PLAN_OFFSETS["lords_day_window_hours"]))
        if slot <= now < end:
            return (slot, end)
    return None


def beta_started(season: Any, now: datetime) -> bool:
    """Whether the armed beta has actually started (T0 reached).

    beta-weekend-v2: the compressed config applies at T0, not at the
    arming — this gate drives both the scheduler's preset
    (re)application and the console status lines. A season armed
    without ``beta_start_at`` (legacy v1) counts as started.
    """
    if not getattr(season, "beta_name", None):
        return False
    start = getattr(season, "beta_start_at", None)
    if start is None:
        return True
    return bool(_utc(now) >= _utc(start))
