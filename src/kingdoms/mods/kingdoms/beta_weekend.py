"""Beta weekend presets (beta-weekend-v1, Drasah 17/10/2026).

Drasah opens a multi-user beta test on a weekend (Friday 20:00 →
Monday 00:00, Europe/Paris): the whole season — 2 cycles, one age
switch, the official end with results and rewards — is compressed
into 52 hours. The console command ``lancer_beta <nom>`` activates a
NAMED preset: the shared season config is mutated in place (every
service reads it live) and the explicit slot plan is persisted on the
season document, so a restarted process re-applies the preset on its
first scheduler tick. A normal season is untouched — nothing changes
unless the console says so.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
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

# Preset « 1.0 » (Drasah 17/10/2026) — decisions verbatim:
# 2 attaques / 2 défenses, Sombre → Féodal at the Friday/Saturday
# midnight, two Lord's Day cycles between 04:00 and 08:00 (6:00),
# special actions free and unlimited, season end Monday 00:00.
BETA_PRESETS: dict[str, dict[str, Any]] = {
    "1.0": {
        "attacks": {
            "attacks_per_week": 2,
            "defenses_per_week": 2,
            "player_attack_delay_hours": 1,
            "gaia_attack_delay_hours": 1,
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
        # The weekend slot plan (weekday: 0=Monday … 5=Saturday,
        # 6=Sunday), everything in Europe/Paris.
        "plan": {
            "age": (5, 0),  # Saturday 00:00 — Sombre → Féodal
            "cycles": [(5, 6), (6, 6)],  # Sat 06:00 + Sun 06:00 — Jour du Seigneur
            "end": (0, 0),  # Monday 00:00 — official season end
            "lords_day_window_hours": 2,  # attacks blocked 6h00 → 8h00
        },
    },
}


def beta_preset_names() -> tuple[str, ...]:
    """Return the available preset names (console help / error messages)."""
    return tuple(sorted(BETA_PRESETS))


def apply_beta_preset(config: KingdomsSeasonConfig, name: str) -> None:
    """Mutate the shared season config in place onto the preset.

    Raises ``ValueError`` on an unknown preset — a typo in the console
    must never silently run a normal-paced weekend. Every service holds
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


def _next_slot(now: datetime, weekday: int, hour: int, tz: ZoneInfo) -> datetime:
    """Return the first (weekday, hour) slot in Paris STRICTLY after ``now``."""
    local = now.astimezone(tz)
    day = local.date()
    for _ in range(8):
        slot = datetime.combine(day, time(hour, 0), tzinfo=tz)
        if slot > local and day.weekday() == weekday:
            return slot.astimezone(tz)
        day += timedelta(days=1)
    raise RuntimeError("beta slot search failed")  # pragma: no cover


def plan_beta_slots(name: str, now: datetime) -> dict[str, Any]:
    """Compute the weekend slot plan anchored on the activation moment.

    ``now`` is the activation timestamp (Friday 20:00 in Drasah's
    flow): the age switch lands on the coming Saturday midnight, the
    two Lord's Day cycles on the coming Saturday and Sunday mornings,
    and the season end on the coming Monday at 00:00 Europe/Paris.
    """
    preset = BETA_PRESETS.get(name)
    if preset is None:
        raise ValueError(f"preset beta inconnu : {name!r} (disponibles : {', '.join(beta_preset_names())})")
    plan = preset["plan"]
    try:
        tz = ZoneInfo(BETA_TIMEZONE)
    except Exception:  # pragma: no cover - the stock tz database has it
        tz = ZoneInfo("UTC")
    if now.tzinfo is None:
        now = now.replace(tzinfo=ZoneInfo("UTC"))
    age_at = _next_slot(now, plan["age"][0], plan["age"][1], tz)
    cycles = [_next_slot(now, weekday, hour, tz) for weekday, hour in plan["cycles"]]
    end_at = _next_slot(cycles[-1], plan["end"][0], plan["end"][1], tz)
    return {
        "age_at": age_at,
        "cycle_slots": cycles,
        "end_at": end_at,
        "lords_day_window_hours": plan["lords_day_window_hours"],
    }


def beta_lords_day_window(
    season: Any,
    now: datetime,
) -> tuple[datetime, datetime] | None:
    """Return the beta Lord's Day window around the last consumed cycle slot.

    Each beta cycle slot opens a short protected window (2h by default)
    where no attack is PLAYED — the compressed equivalent of the
    Sunday 23:30 → Monday 10:00 season window. Returns None outside
    the windows (and on an unknown preset name — never block a season
    on a missing preset).
    """
    preset = BETA_PRESETS.get(str(getattr(season, "beta_name", None) or ""))
    if preset is None:
        return None
    if now.tzinfo is None:
        now = now.replace(tzinfo=ZoneInfo("UTC"))
    for slot in list(getattr(season, "beta_cycle_slots", None) or []):
        end = slot + timedelta(hours=int(preset["plan"]["lords_day_window_hours"]))
        if slot <= now < end:
            return (slot, end)
    return None
