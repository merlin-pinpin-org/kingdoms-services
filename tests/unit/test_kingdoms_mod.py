"""Kingdoms mod foundations — unit tests (T1, kingdoms-services#156).

Covers the two acceptance criteria testable without Discord: the
season config is data (changing it changes behavior, not code) and the
season-state boundary (models round-trip, tech invariants D36)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from kingdoms.core.services.mod_registry import load_mod_definitions
from kingdoms.mods.kingdoms.config import (
    default_season_config,
    load_season_config,
)
from kingdoms.mods.kingdoms.models import (
    GAIA_KINGDOM_KEY,
    KingdomModel,
    KingdomType,
    LordModel,
    LordRole,
    SeasonState,
    TechnologyState,
    TerritoryModel,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def season_config() -> object:
    return default_season_config()


class TestModDeclaration:
    """The mod declaration validates and declares the documented
    surfaces/roles (docs/MODS/kingdoms/ENVIRONMENT.md)."""

    def test_declaration_loads_and_validates(self) -> None:
        defs = load_mod_definitions(REPO_ROOT / "config")
        kingdoms = defs["kingdoms"]
        assert kingdoms.enabled is True  # T2 enrollment lands (kingdoms-services#157)
        # restructuration-serveur-v1 c+d+e (Drasah 19/10): the announce,
        # exploration, patrol, presentation, rules, season, suggestions,
        # update and tavern salons are gone (Gaïa keeps a single
        # territory salon; the general group is gone entirely).
        # permissions-serveur-v1 (Drasah 19/10): the epoch salon retires
        # too — the age lives in Géopolitique (pinned message + emoji).
        assert {c.key for c in kingdoms.channel_categories} == {
            "applications",
            "apply",
            "attack",
            "distributor",
            "console",  # admin-console-v1: the 🛠 console-admin salon
            "geopolitics",
            "market",
            "realms",
            "requests",
            "season_management",
            "settings",
            "scriptorium",
            "talks",
            "territory",
            "time_machine",
        }
        assert {r.key for r in kingdoms.roles} == {
            "kingdoms_king",
            "kingdoms_lord",
            "kingdoms_admin",
            "kingdoms_locked",
        }

    def test_access_policies_declared(self) -> None:
        """permissions-serveur-v1 (Drasah 19/10): the gating is DECLARED.

        A newcomer sees only Conscription and Beta — every other salon
        denies @everyone and unlocks through the player roles (granted
        at acceptance); the admin salons open only to Kingdoms Admin.
        """
        defs = load_mod_definitions(REPO_ROOT / "config")
        channels = {c.key: c for c in defs["kingdoms"].channel_categories}
        # The player salons: everyone denied, unlocked by the roles.
        for key in ("scriptorium", "market", "geopolitics", "territory", "attack"):
            policy = channels[key].access
            assert policy is not None
            assert policy.everyone_view is False
            assert "Kingdoms Lord" in policy.view
            assert "Kingdoms Admin" in policy.view
        # The talks salon also carries post rights.
        talks = channels["talks"].access
        assert talks is not None and "Kingdoms King" in talks.post
        # The admin salons: admin-only view.
        for key in ("settings", "season_management", "time_machine", "distributor", "console"):
            policy = channels[key].access
            assert policy is not None
            assert policy.everyone_view is False
            assert list(policy.view) == ["Kingdoms Admin"]
        # The conscription path stays public: a newcomer must see it.
        assert channels["apply"].access is None or channels["apply"].access.everyone_view is True


class TestSeasonConfig:
    """The season config is data: Season II defaults, admin-overridable
    without a code change (reference §27/§28, D9)."""

    def test_defaults_are_season_ii(self, season_config) -> None:
        assert season_config.weeks == 4
        assert season_config.kingdoms_count == 2
        assert season_config.lords_per_kingdom == 4
        assert season_config.territories_per_kingdom == 5
        assert season_config.gaia_territories == 8
        assert season_config.garrison_enabled is False

    def test_default_ages_follow_d18(self, season_config) -> None:
        levels = [age.gaia_ai_level for age in season_config.ages]
        assert levels == [2, 3, 5, 5]
        assert [age.extra_marriages for age in season_config.ages] == [1, 1, 1, 1]
        assert [age.tech_points for age in season_config.ages] == [0, 1, 2, 2]

    def test_default_technology_costs_follow_reference(self, season_config) -> None:
        tech = season_config.technologies
        assert tech.embuscade == 2
        assert tech.corruption == 4
        assert tech.mariage_arrange == 3

    def test_admin_override_without_code(self, tmp_path) -> None:
        season_file = tmp_path / "kingdoms" / "season.yaml"
        season_file.parent.mkdir()
        season_file.write_text("kingdoms_count: 3\nweeks: 6\n")
        config = load_season_config(tmp_path)
        assert config.kingdoms_count == 3
        assert config.weeks == 6
        assert config.lords_per_kingdom == 4  # untouched default

    def test_missing_file_gives_defaults(self, tmp_path) -> None:
        assert load_season_config(tmp_path) == default_season_config()

    def test_invalid_override_fails_loudly(self, tmp_path) -> None:
        season_file = tmp_path / "kingdoms" / "season.yaml"
        season_file.parent.mkdir()
        season_file.write_text("kingdoms_count: -1\n")
        with pytest.raises(Exception, match="kingdoms_count"):
            load_season_config(tmp_path)


class TestSeasonModels:
    """Season-state models: round-trip, Gaïa invariants (D32), tech
    invariants (D36)."""

    def test_kingdom_round_trip(self) -> None:
        kingdom = KingdomModel(
            _id="k-a",
            season_id="s1",
            type=KingdomType.PLAYER,
            name="Royaume A",
            marriage_capacity=1,
        )
        rebuilt = KingdomModel.from_mongo(kingdom.to_mongo())
        assert rebuilt == kingdom
        assert rebuilt.is_gaia is False

    def test_gaia_kingdom_is_detectable(self) -> None:
        gaia = KingdomModel(
            _id=GAIA_KINGDOM_KEY,
            season_id="s1",
            type=KingdomType.GAIA,
            name="Gaïa",
        )
        assert gaia.is_gaia is True

    def test_lord_round_trip_with_weekly_budgets(self) -> None:
        lord = LordModel(
            _id="p-1",
            season_id="s1",
            kingdom_id="k-a",
            role=LordRole.KING,
            display_name="Aldric",
        )
        rebuilt = LordModel.from_mongo(lord.to_mongo())
        assert rebuilt == lord
        assert rebuilt.attack_used == 0

    def test_territory_round_trip(self) -> None:
        territory = TerritoryModel(
            _id="t-1",
            season_id="s1",
            map_key="arabia",
            owner_kingdom_id=GAIA_KINGDOM_KEY,
            drawn_at=datetime(2026, 10, 4, tzinfo=UTC),
        )
        assert TerritoryModel.from_mongo(territory.to_mongo()) == territory

    def test_season_state_round_trip(self) -> None:
        state = SeasonState(
            _id="s1",
            started_at=datetime(2026, 10, 1, tzinfo=UTC),
            weeks=4,
            current_age_key="dark_age",
        )
        assert SeasonState.from_mongo(state.to_mongo()) == state

    def test_technology_spend_enforces_limit(self) -> None:
        tech = TechnologyState(
            _id="k-a",
            season_id="s1",
            tech_points=5,
            purchases={},
        )
        assert tech.spend("corruption", 4, limit=1) is True
        assert tech.tech_points == 1
        assert tech.purchases == {"corruption": 1}
        # Limit reached (D36): a second purchase is refused even if
        # the points were there.
        assert tech.spend("corruption", 4, limit=1) is False

    def test_technology_spend_enforces_affordability(self) -> None:
        tech = TechnologyState(_id="k-a", season_id="s1", tech_points=1)
        assert tech.spend("embuscade", 2, limit=0) is False
        assert tech.tech_points == 1

    def test_technology_zero_limit_is_unlimited(self) -> None:
        tech = TechnologyState(_id="k-a", season_id="s1", tech_points=3)
        assert tech.spend("traquenard", 1, limit=0) is True
        assert tech.spend("traquenard", 1, limit=0) is True
        assert tech.spend("traquenard", 1, limit=0) is True
        # Not limited (0 = unlimited) but now broke.
        assert tech.spend("traquenard", 1, limit=0) is False
