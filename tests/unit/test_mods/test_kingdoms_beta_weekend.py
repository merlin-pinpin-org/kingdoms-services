"""Kingdoms beta weekend preset — unit tests (beta-weekend-v2, Drasah 17/10).

Preset « now »: a 52-hour beta that starts at an EXACT moment the
console interface captures — age at T0+4h, two Lord's Day cycles at
T0+10h/T0+34h, official end (results + rewards) at T0+52h. Behind an
in-memory store: the config mutation, the delayed application (the
compressed config applies AT T0, not at the arming), the restart-safe
re-application and the full weekend replay.
"""
from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from kingdoms.mods.kingdoms.beta_weekend import (
    apply_beta_preset,
    beta_lords_day_window,
    beta_preset_names,
    beta_started,
    parse_beta_start,
    plan_beta_slots,
)
from kingdoms.mods.kingdoms.config import KingdomsSeasonConfig, default_season_config
from kingdoms.mods.kingdoms.events import EventService, active_lords_day_window

from .test_kingdoms_events import Bundle, _config

PARIS = ZoneInfo("Europe/Paris")


def test_preset_now_mutates_the_shared_config() -> None:
    """The preset compresses every domain without touching the code."""
    config = default_season_config()
    apply_beta_preset(config, "now")
    attacks = config.attacks
    # planification-attaque-v1 (Drasah 23/10): the beta opens on TEN
    # kingdoms by default — no extra admin action needed.
    assert config.kingdoms_count == 10
    assert attacks.attacks_per_week == 10
    assert attacks.defenses_per_week == 10
    assert attacks.player_attack_delay_minutes == 10
    assert attacks.gaia_attack_delay_minutes == 10
    assert attacks.embuscade_delay_minutes == 10
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


def test_delay_defaults_stay_season_ii() -> None:
    """Out of a beta, the config defaults keep the Season II values —
    the minutes migration (6h/3h/1h) changes nothing at runtime."""
    config = default_season_config()
    assert config.attacks.player_attack_delay_minutes == 360
    assert config.attacks.gaia_attack_delay_minutes == 180
    assert config.attacks.embuscade_delay_minutes == 60
    assert config.attacks.ready_grace_minutes == 5
    assert config.attacks.score_reminder_hours == 3.0
    assert config.attacks.score_dispute_after_reminder_hours == 12.0


def test_legacy_preset_1_0_stays_loadable() -> None:
    """A season armed with the v1 « 1.0 » preset never breaks: the
    legacy name stays loadable with its own (1h) delays."""
    config = default_season_config()
    apply_beta_preset(config, "1.0")
    assert config.attacks.player_attack_delay_minutes == 60
    assert config.attacks.attacks_per_week == 10


def test_unknown_preset_fails_loudly() -> None:
    """A typo in the console never silently runs a normal-paced beta."""
    config = default_season_config()
    with pytest.raises(ValueError, match="preset beta inconnu"):
        apply_beta_preset(config, "9.9")


def test_parse_beta_start_accepts_the_console_formats() -> None:
    """« JJ/MM HH:MM », « JJ/MM/AAAA HH:MM » and « maintenant »."""
    now = datetime(2026, 10, 17, 10, 0, tzinfo=PARIS)
    start = parse_beta_start("23/10 20:00", now)
    assert start == datetime(2026, 10, 23, 20, 0, tzinfo=PARIS)
    explicit = parse_beta_start("23/10/2026 20:00", now)
    assert explicit == start
    immediate = parse_beta_start("maintenant", now)
    assert immediate == now


def test_parse_beta_start_refuses_the_past_and_garbage() -> None:
    """A start in the past (or an unparseable one) is refused."""
    now = datetime(2026, 10, 17, 10, 0, tzinfo=PARIS)
    with pytest.raises(ValueError, match="futur"):
        parse_beta_start("16/10 20:00", now)
    with pytest.raises(ValueError, match="format"):
        parse_beta_start("hier soir", now)
    with pytest.raises(ValueError, match="invalide"):
        parse_beta_start("32/13 20:00", now)


def test_slot_plan_rides_the_exact_start() -> None:
    """The plan rides T0 exactly: +4h age, +10h/+34h cycles, +52h end."""
    start = datetime(2026, 10, 23, 20, 0, tzinfo=PARIS)
    plan = plan_beta_slots("now", start)
    assert plan["start_at"] == start
    assert plan["age_at"] == datetime(2026, 10, 24, 0, 0, tzinfo=PARIS)
    assert plan["cycle_slots"] == [
        datetime(2026, 10, 24, 6, 0, tzinfo=PARIS),
        datetime(2026, 10, 25, 6, 0, tzinfo=PARIS),
    ]
    assert plan["end_at"] == datetime(2026, 10, 26, 0, 0, tzinfo=PARIS)


async def test_armed_beta_waits_for_t0() -> None:
    """An armed-not-started beta leaves the season at its normal pace
    until T0 — the compressed config applies at T0, not at the arming."""
    bundle = Bundle()
    await bundle.launch_season()
    start = datetime(2026, 10, 23, 20, 0, tzinfo=PARIS)
    report = await bundle.events.activate_beta("now", start_at=start, now=datetime(2026, 10, 17, 12, 0, tzinfo=PARIS))
    assert report["armed"] is True
    assert bundle.config.attacks.attacks_per_week == 1  # normal pace
    season = await bundle.kingdoms.current_season()
    assert season is not None
    assert season.beta_name == "now"
    assert beta_started(season, datetime(2026, 10, 23, 19, 59, tzinfo=PARIS)) is False
    # Before T0 the scheduler replays nothing and keeps the normal pace.
    await bundle.events.run_due(datetime(2026, 10, 20, 12, 0, tzinfo=UTC))
    assert bundle.config.attacks.attacks_per_week == 1
    # The first tick past T0 applies the preset.
    await bundle.events.run_due(datetime(2026, 10, 23, 20, 5, tzinfo=PARIS).astimezone(UTC))
    assert bundle.config.attacks.attacks_per_week == 10
    assert bundle.config.attacks.player_attack_delay_minutes == 10


async def test_immediate_start_applies_the_preset_right_away() -> None:
    """A start at « maintenant » applies the compressed config at once."""
    bundle = Bundle()
    await bundle.launch_season()
    now = datetime(2026, 10, 23, 20, 0, tzinfo=PARIS)
    report = await bundle.events.activate_beta("now", start_at=now, now=now)
    assert report["armed"] is False
    assert bundle.config.attacks.attacks_per_week == 10
    assert bundle.config.attacks.player_attack_delay_minutes == 10


async def test_beta_weekend_replays_age_cycles_then_official_end() -> None:
    """The whole 52h in one catch-up: age, 2 cycles, the end verdict
    with results — and nothing replays after it."""
    bundle = Bundle()
    await bundle.launch_season()
    start = datetime(2026, 10, 23, 20, 0, tzinfo=PARIS)
    await bundle.events.activate_beta("now", start_at=start, now=start)
    now = datetime(2026, 10, 26, 0, 30, tzinfo=PARIS).astimezone(UTC)
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
    start = datetime(2026, 10, 23, 20, 0, tzinfo=PARIS)
    await bundle.events.activate_beta("now", start_at=start, now=start)
    reports = await bundle.events.run_due(datetime(2026, 10, 24, 0, 30, tzinfo=PARIS).astimezone(UTC))
    assert [r["kind"] for r in reports] == ["age"]
    reports = await bundle.events.run_due(datetime(2026, 10, 24, 6, 30, tzinfo=PARIS).astimezone(UTC))
    assert [r["kind"] for r in reports] == ["cycle"]
    assert await bundle.events.run_due(datetime(2026, 10, 24, 6, 30, tzinfo=PARIS).astimezone(UTC)) == []


async def test_beta_lords_day_windows_follow_the_plan() -> None:
    """Each cycle slot opens a 2h protected window — none outside."""
    bundle = Bundle()
    await bundle.launch_season()
    start = datetime(2026, 10, 23, 20, 0, tzinfo=PARIS)
    await bundle.events.activate_beta("now", start_at=start, now=start)
    season = await bundle.kingdoms.current_season()
    assert season is not None
    config: KingdomsSeasonConfig = bundle.config
    inside = datetime(2026, 10, 24, 7, 0, tzinfo=PARIS).astimezone(UTC)
    window = active_lords_day_window(config, season, inside)
    assert window is not None
    assert beta_lords_day_window(season, inside) == window
    assert window[0] == datetime(2026, 10, 24, 6, 0, tzinfo=PARIS)
    assert window[1] == datetime(2026, 10, 24, 8, 0, tzinfo=PARIS)
    outside = datetime(2026, 10, 24, 9, 0, tzinfo=PARIS).astimezone(UTC)
    assert active_lords_day_window(config, season, outside) is None


async def test_beta_preset_reapplies_after_a_restart() -> None:
    """A restarted process re-applies the preset on its first tick
    past T0 — and only then."""
    bundle = Bundle()
    await bundle.launch_season()
    start = datetime(2026, 10, 23, 20, 0, tzinfo=PARIS)
    await bundle.events.activate_beta("now", start_at=start, now=start)
    assert bundle.config.attacks.attacks_per_week == 10
    fresh_config = _config()
    events = EventService(bundle.store, fresh_config, bundle.kingdoms, bundle.territories, bundle.attacks)
    assert fresh_config.attacks.attacks_per_week == 1
    await events.run_due(datetime(2026, 10, 24, 0, 30, tzinfo=PARIS).astimezone(UTC))
    assert fresh_config.attacks.attacks_per_week == 10
    assert fresh_config.attacks.player_attack_delay_minutes == 10
    assert [age.key for age in fresh_config.ages] == ["dark_age", "feudal_age"]


def test_preset_names_advertise_now_first() -> None:
    """The console help lists « now » (the legacy 1.0 stays loadable)."""
    assert beta_preset_names()[0] == "now"
    assert "1.0" in beta_preset_names()


async def test_beta_plan_survives_the_naive_mongo_round_trip() -> None:
    """The MongoDB round-trip strips the tzinfo (a BSON datetime reloads
    as naive UTC) — the window and T0 comparisons with the aware game
    clock must never crash (live 17/10: « can't compare offset-naive
    and offset-aware datetimes » on the attack path)."""
    bundle = Bundle()
    await bundle.launch_season()
    start = datetime(2026, 10, 23, 20, 0, tzinfo=PARIS)
    await bundle.events.activate_beta("now", start_at=start, now=start)
    season = await bundle.kingdoms.current_season()
    assert season is not None
    # The BSON wire shape: every stored datetime comes back naive (UTC).
    document = season.to_mongo()
    for key in ("started_at", "beta_start_at", "beta_age_at", "beta_end_at"):
        if document.get(key) is not None:
            document[key] = document[key].astimezone(UTC).replace(tzinfo=None)
    document["beta_cycle_slots"] = [slot.astimezone(UTC).replace(tzinfo=None) for slot in document["beta_cycle_slots"]]
    await bundle.store.upsert_season(document)
    reloaded = await bundle.kingdoms.current_season()
    assert reloaded is not None
    assert reloaded.beta_start_at is not None
    # The from_mongo seam re-attaches UTC to the naive wire values
    # (models._aware_utc, Check 69) — aware on every path now.
    assert reloaded.beta_start_at.tzinfo is UTC

    config: KingdomsSeasonConfig = bundle.config
    aware_now = datetime(2026, 10, 24, 7, 0, tzinfo=PARIS).astimezone(UTC)
    # The attack path: the window comparison must answer, not raise.
    assert active_lords_day_window(config, reloaded, aware_now) is not None
    assert beta_lords_day_window(reloaded, aware_now) is not None
    assert beta_started(reloaded, aware_now) is True
    # The scheduler path: run_due replays the due slots without raising,
    # whatever naive datetimes the plan carries.
    reports = await bundle.events.run_due(aware_now)
    assert [report["kind"] for report in reports] == ["age", "cycle"]
    after = await bundle.kingdoms.current_season()
    assert after is not None
    assert after.current_age_key == "feudal_age"
    assert after.current_cycle == 2
