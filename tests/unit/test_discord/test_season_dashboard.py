"""Unit tests for the Gestion-saison dashboard + Temps de saison (#214 phase 1.1)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from kingdoms.discord.season_dashboard import (
    SEASON_DASHBOARD_MARKER,
    SEASON_TIME_MARKER,
    SeasonSnapshot,
    build_dashboard_content,
    build_time_content,
    discord_timestamp,
    next_cycle_at,
    refresh_season_dashboard,
    refresh_season_time,
    season_end_at,
    snapshot_from_services,
)
from kingdoms.mods.kingdoms.config import default_season_config
from kingdoms.mods.kingdoms.models import SeasonState
from tests.mocks.discord_mock import MockGuild
from tests.mocks.provision import provisioned_wiring

UTC = UTC
START = datetime(2026, 10, 8, 20, 0, tzinfo=UTC)


def _season() -> SeasonState:
    return SeasonState(
        _id="season-20261008-2000",
        started_at=START,
        weeks=3,
        current_cycle=0,
        current_age_key="dark_age",
    )


class _FakeService:
    """The seam KingdomsService exposes to the dashboard."""

    def __init__(self, season: SeasonState | None) -> None:
        self._season = season
        self.config = default_season_config()

    async def current_season(self) -> SeasonState | None:
        return self._season

    async def kingdoms(self) -> list[Any]:
        class _K:
            def __init__(self, gaia: bool) -> None:
                self.is_gaia = gaia

        return [_K(True), _K(False), _K(False)]

    async def lords(self) -> list[Any]:
        class _L:
            def __init__(self, left: bool, in_queue: bool) -> None:
                self.left = left
                self.in_queue = in_queue

        return [_L(False, False), _L(False, False), _L(False, True), _L(True, False)]


def _snapshot(**overrides: Any) -> SeasonSnapshot:
    base = {
        "season_id": "season-20261008-2000",
        "cycle": 1,
        "total_cycles": 3,
        "age_name": "Âge sombre",
        "started_at": START,
        "jour_j": START + timedelta(weeks=1),
        "ends_at": START + timedelta(weeks=3),
        "paused": False,
        "kingdoms": 2,
        "active_lords": 2,
        "queued": 1,
    }
    base.update(overrides)
    return SeasonSnapshot(**base)


# ------------------------------------------------------------------
# Schedule math (D1/D55)
# ------------------------------------------------------------------


def test_next_cycle_is_one_week_per_cycle() -> None:
    """Jour J: one week per cycle since the launch."""
    assert next_cycle_at(START, 0) == START + timedelta(weeks=1)
    assert next_cycle_at(START, 2) == START + timedelta(weeks=3)


def test_naive_datetimes_read_as_utc() -> None:
    naive = START.replace(tzinfo=None)
    assert next_cycle_at(naive, 0) == START + timedelta(weeks=1)


def test_season_end_is_weeks_cycles_after_launch() -> None:
    """D55: 3 weeks = 3 cycles."""
    assert season_end_at(START, 3) == START + timedelta(weeks=3)


def test_discord_timestamp_renders_epoch_and_style() -> None:
    assert discord_timestamp(START, "R") == f"<t:{int(START.timestamp())}:R>"


# ------------------------------------------------------------------
# Content rendering
# ------------------------------------------------------------------


def test_dashboard_content_fr_shows_the_full_board() -> None:
    content = build_dashboard_content(_snapshot(), "fr")
    assert SEASON_DASHBOARD_MARKER in content
    assert "Cycle** : 1/3" in content
    assert "Âge** : Âge sombre" in content
    assert "🟢 active" in content
    assert "2 royaumes" in content and "2 seigneurs actifs" in content and "1 en attente" in content
    assert f"<t:{int((START + timedelta(weeks=1)).timestamp())}:F>" in content


def test_dashboard_content_paused_and_en() -> None:
    content = build_dashboard_content(_snapshot(paused=True), "en")
    assert "🟠 paused" in content
    assert SEASON_DASHBOARD_MARKER in content


def test_dashboard_content_without_season() -> None:
    content = build_dashboard_content(None, "fr")
    assert "Aucune saison en cours" in content
    assert SEASON_DASHBOARD_MARKER in content


def test_time_content_fr_lists_the_live_timers() -> None:
    content = build_time_content(_snapshot(), "fr")
    assert SEASON_TIME_MARKER in content
    assert "Prochain Jour J**" in content
    assert "Fin de la saison**" in content
    assert f"<t:{int((START + timedelta(weeks=3)).timestamp())}:F>" in content


def test_time_content_without_season() -> None:
    content = build_time_content(None, "fr")
    assert "Aucune saison en cours" in content
    assert SEASON_TIME_MARKER in content


# ------------------------------------------------------------------
# Snapshot from the service
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snapshot_from_services_counts_the_roster() -> None:
    snapshot = await snapshot_from_services(_FakeService(_season()))
    assert snapshot is not None
    assert snapshot.cycle == 1
    assert snapshot.total_cycles == 3
    assert snapshot.age_name == "Âge sombre"
    assert snapshot.paused is False  # phase 1.2 adds the pause flag
    assert snapshot.kingdoms == 2
    assert snapshot.active_lords == 2
    assert snapshot.queued == 1
    assert snapshot.jour_j == START + timedelta(weeks=1)
    assert snapshot.ends_at == START + timedelta(weeks=3)


@pytest.mark.asyncio
async def test_snapshot_without_season_or_service() -> None:
    assert await snapshot_from_services(None) is None
    assert await snapshot_from_services(_FakeService(None)) is None


# ------------------------------------------------------------------
# Channel refresh (marker contract: one message, edited in place)
# ------------------------------------------------------------------


async def _provisioned_guild() -> Any:
    guild = MockGuild(id=1)
    provisioned_wiring(guild)
    from kingdoms.discord.kingdom_setup import provision_structure

    await provision_structure(guild)
    return guild


@pytest.mark.asyncio
async def test_refresh_posts_then_edits_in_place() -> None:
    guild = await _provisioned_guild()
    dashboard = next(c for c in guild.text_channels if c.name == "gestion-saison")
    timers = next(c for c in guild.text_channels if c.name == "temps-de-saison")

    assert await refresh_season_dashboard(guild, "fr", _FakeService(_season()))
    assert await refresh_season_time(guild, "fr", _FakeService(_season()))
    assert len(dashboard.messages) == 1
    assert len(timers.messages) == 1
    assert "1/3" in dashboard.messages[0].content
    assert SEASON_TIME_MARKER in timers.messages[0].content

    # A second refresh edits the same message, never duplicates it.
    assert await refresh_season_dashboard(guild, "fr", _FakeService(_season()))
    assert await refresh_season_time(guild, "fr", _FakeService(_season()))
    assert len(dashboard.messages) == 1
    assert len(timers.messages) == 1
    assert dashboard.messages[0]._edited is True
    assert timers.messages[0]._edited is True


@pytest.mark.asyncio
async def test_refresh_without_season_still_deploys_the_messages() -> None:
    guild = await _provisioned_guild()
    assert await refresh_season_dashboard(guild, "fr", None)
    assert await refresh_season_time(guild, "fr", None)
    dashboard = next(c for c in guild.text_channels if c.name == "gestion-saison")
    timers = next(c for c in guild.text_channels if c.name == "temps-de-saison")
    assert "Aucune saison en cours" in dashboard.messages[0].content
    assert "Aucune saison en cours" in timers.messages[0].content


@pytest.mark.asyncio
async def test_refresh_without_channel_reports_not_deployed() -> None:
    guild = MockGuild(id=7)
    assert await refresh_season_dashboard(guild, "fr", None) is False
    assert await refresh_season_time(guild, "fr", None) is False


@pytest.mark.asyncio
async def test_deploy_panels_deploys_the_two_new_surfaces() -> None:
    from kingdoms.discord.kingdom_panels import deploy_panels

    guild = await _provisioned_guild()

    class _Logs:
        async def get_locale(self, guild_id: str) -> str:
            return "fr"

    report = await deploy_panels(guild, _Logs(), bot_admins=("111",), kingdoms_service=_FakeService(_season()))
    assert report.get("gestion-saison") == "deployed"
    assert report.get("temps de saison") == "deployed"
    dashboard = next(c for c in guild.text_channels if c.name == "gestion-saison")
    assert "1/3" in dashboard.messages[0].content
