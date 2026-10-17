"""Kingdoms beta weekend preset — unit tests (beta-weekend-v1, Drasah 17/10).

Preset « 1.0 »: compressed weekend (Friday 20:00 → Monday 00:00 Paris)
behind an in-memory store — the config mutation, the explicit slot
plan, the Lord's Day windows, the restart-safe preset re-application
and the full weekend replay (age, cycles, official end with results).
"""
from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from kingdoms.mods.kingdoms.beta_weekend import (
    apply_beta_preset,
    beta_lords_day_window,
    beta_preset_names,
    plan_beta_slots,
)
from kingdoms.mods.kingdoms.config import KingdomsSeasonConfig, default_season_config
from kingdoms.mods.kingdoms.events import EventService, active_lords_day_window

from .test_kingdoms_events import Bundle, _config

PARIS = ZoneInfo("Europe/Paris")


def test_preset_1_0_mutates_the_shared_config() -> None:
    """The preset compresses every domain without touching the code."""
    config = default_season_config()
    apply_beta_preset(config, "1.0")
    attacks = config.attacks
    assert attacks.attacks_per_week == 2
    assert attacks.defenses_per_week == 2
    assert attacks.player_attack_delay_hours == 1
    assert attacks.defense_cutoff_minutes == 5
    assert attacks.ready_grace_minutes == 3
    assert attacks.score_reminder_hours == 0.5
    assert attacks.score_dispute_after_reminder_hours == 1.0
    assert [age.key for age in config.ages] == ["dark_age", "feudal_age"]
    assert config.technologies.corruption == 0
    assert config.technologies.limits == {}
    assert config.voliere.corbeau_civ_cooldown_hours == 4
    assert config.parish.cathedral_chantier_hours == 8
    assert [tier.marriage_lock_hours for tier in config.parish.tiers] == [4, 2, 1]


def test_preset_defaults_stay_season_ii() -> None:
    """Out of a beta, the config defaults keep the Season II values."""
    config = default_season_config()
    assert config.attacks.ready_grace_minutes == 5
    assert config.attacks.score_reminder_hours == 3.0
    assert config.attacks.score_dispute_after_reminder_hours == 12.0


def test_unknown_preset_fails_loudly() -> None:
    """A typo in the console never silently runs a normal-paced weekend."""
    config = default_season_config()
    with pytest.raises(ValueError, match="preset beta inconnu"):
        apply_beta_preset(config, "9.9")


def test_slot_plan_is_the_drash_weekend() -> None:
    """Activated Friday 20:00 Paris: age Sat 00:00, cycles Sat+Sun 06:00,
    official end Monday 00:00."""
    plan = plan_beta_slots("1.0", datetime(2026, 10, 16, 20, 0, tzinfo=PARIS))
    assert plan["age_at"] == datetime(2026, 10, 17, 0, 0, tzinfo=PARIS)
    assert plan["cycle_slots"] == [
        datetime(2026, 10, 17, 6, 0, tzinfo=PARIS),
        datetime(2026, 10, 18, 6, 0, tzinfo=PARIS),
    ]
    assert plan["end_at"] == datetime(2026, 10, 19, 0, 0, tzinfo=PARIS)


async def test_activate_beta_persists_the_plan() -> None:
    """`lancer_beta 1.0` mutates the config and persists the plan."""
    bundle = Bundle()
    await bundle.launch_season()
    report = await bundle.events.activate_beta(
        "1.0", now=datetime(2026, 10, 16, 20, 0, tzinfo=PARIS)
    )
    assert report["name"] == "1.0"
    assert report["attacks_per_week"] == 2
    assert report["ages"] == ["dark_age", "feudal_age"]
    season = await bundle.kingdoms.current_season()
    assert season is not None
    assert season.beta_name == "1.0"
    assert season.beta_age_at == datetime(2026, 10, 17, 0, 0, tzinfo=PARIS)
    assert season.beta_end_at == datetime(2026, 10, 19, 0, 0, tzinfo=PARIS)
    assert len(season.beta_cycle_slots) == 2


async def test_beta_weekend_replays_age_cycles_then_official_end() -> None:
    """The whole weekend in one catch-up: age, 2 cycles, the Monday end
    verdict with results — and nothing replays after it."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.events.activate_beta(
        "1.0", now=datetime(2026, 10, 16, 20, 0, tzinfo=PARIS)
    )
    now = datetime(2026, 10, 19, 0, 30, tzinfo=PARIS).astimezone(UTC)
    reports = await bundle.events.run_due(now)
    assert [r["kind"] for r in reports] == ["age", "cycle", "cycle", "end"]
    assert [r["cycle"] for r in reports if r["kind"] == "cycle"] == [2, 3]
    season = await bundle.kingdoms.current_season()
    assert season is not None
    assert season.current_age_key == "feudal_age"
    assert season.finished is True
    assert season.phase == "ended"
    assert "winner_name" in reports[-1]
    assert await bundle.events.run_due(now) == []


async def test_beta_slots_replay_progressively() -> None:
    """Each slot fires exactly once at its own moment."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.events.activate_beta(
        "1.0", now=datetime(2026, 10, 16, 20, 0, tzinfo=PARIS)
    )
    # Saturday 00:30 Paris — the age switch only.
    reports = await bundle.events.run_due(datetime(2026, 10, 17, 0, 30, tzinfo=PARIS).astimezone(UTC))
    assert [r["kind"] for r in reports] == ["age"]
    # Saturday 06:30 Paris — the first cycle.
    reports = await bundle.events.run_due(datetime(2026, 10, 17, 6, 30, tzinfo=PARIS).astimezone(UTC))
    assert [r["kind"] for r in reports] == ["cycle"]
    # Idempotence: the same moment replays nothing.
    assert await bundle.events.run_due(datetime(2026, 10, 17, 6, 30, tzinfo=PARIS).astimezone(UTC)) == []


async def test_beta_lords_day_windows_follow_the_plan() -> None:
    """Each cycle slot opens a 2h protected window — none outside."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.events.activate_beta(
        "1.0", now=datetime(2026, 10, 16, 20, 0, tzinfo=PARIS)
    )
    season = await bundle.kingdoms.current_season()
    assert season is not None
    config: KingdomsSeasonConfig = bundle.config
    inside = datetime(2026, 10, 17, 7, 0, tzinfo=PARIS).astimezone(UTC)
    window = active_lords_day_window(config, season, inside)
    assert window is not None
    assert beta_lords_day_window(season, inside) == window
    assert window[0] == datetime(2026, 10, 17, 6, 0, tzinfo=PARIS)
    assert window[1] == datetime(2026, 10, 17, 8, 0, tzinfo=PARIS)
    outside = datetime(2026, 10, 17, 9, 0, tzinfo=PARIS).astimezone(UTC)
    assert active_lords_day_window(config, season, outside) is None
    # Before the first cycle slot the calendar Sunday window is gone
    # too — the plan replaces the crons entirely.
    before = datetime(2026, 10, 16, 21, 0, tzinfo=PARIS).astimezone(UTC)
    assert active_lords_day_window(config, season, before) is None


async def test_beta_preset_reapplies_after_a_restart() -> None:
    """A restarted process re-applies the preset on its first tick."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.events.activate_beta(
        "1.0", now=datetime(2026, 10, 16, 20, 0, tzinfo=PARIS)
    )
    assert bundle.config.attacks.attacks_per_week == 2
    # A restart: fresh services over the SAME store and a DEFAULT config.
    fresh_config = _config()
    events = EventService(
        bundle.store, fresh_config, bundle.kingdoms, bundle.territories, bundle.attacks
    )
    assert fresh_config.attacks.attacks_per_week == 1
    await events.run_due(datetime(2026, 10, 16, 21, 0, tzinfo=PARIS).astimezone(UTC))
    assert fresh_config.attacks.attacks_per_week == 2
    assert [age.key for age in fresh_config.ages] == ["dark_age", "feudal_age"]


def test_preset_names_listing() -> None:
    """The console help lists the available presets."""
    assert beta_preset_names() == ("1.0",)
