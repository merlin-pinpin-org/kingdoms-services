"""Kingdoms mod Lot C snapshots — unit tests on plain season data.

The projections are pure: models in, view dataclasses out. These tests
pin the designer-visible contracts: the shop derives from the config
costs, the cadastre never crashes on a dangling owner, and the season
label reads the config age names.
"""
from __future__ import annotations

from datetime import UTC, datetime

from kingdoms.mods.kingdoms.config import default_season_config
from kingdoms.mods.kingdoms.models import (
    KingdomModel,
    KingdomType,
    LordModel,
    LordRole,
    SeasonState,
    TechnologyState,
    TerritoryModel,
)
from kingdoms.mods.kingdoms.snapshot import (
    kingdom_card,
    season_label,
    tech_points_of,
    tech_shop,
    territory_lines,
)


def _kingdom(kingdom_id: str = "k-a", name: str = "Aquitaine", kind: KingdomType = KingdomType.PLAYER) -> KingdomModel:
    return KingdomModel(id=kingdom_id, season_id="s1", type=kind, name=name)


def _season() -> SeasonState:
    return SeasonState(
        id="s1",
        started_at=datetime(2026, 10, 1, tzinfo=UTC),
        weeks=4,
        current_cycle=2,
        current_age_key="feudal_age",
    )


def test_tech_shop_derives_from_config_costs() -> None:
    """The shop lists every action with a strictly positive cost."""
    config = default_season_config().technologies
    cards = tech_shop(config, locale="fr")
    assert cards, "the Season II defaults define at least one action"
    keys = {card.key for card in cards}
    assert "embuscade" in keys
    assert all(card.cost > 0 for card in cards)
    declared = set(config.model_dump())
    assert all(card.key in declared for card in cards)


def test_tech_shop_localizes_display_names() -> None:
    """The same config renders French or English display names."""
    config = default_season_config().technologies
    fr = {card.key: card.name for card in tech_shop(config, locale="fr")}
    en = {card.key: card.name for card in tech_shop(config, locale="en")}
    assert fr["embuscade"] == "Embuscade"
    assert en["embuscade"] == "Ambush"


def test_kingdom_card_projects_king_lords_and_marriages() -> None:
    """The profile card reads the king, the lords and the married lords."""
    kingdom = _kingdom()
    lords = (
        LordModel(id="l1", season_id="s1", role=LordRole.KING, display_name="Drasah", married_civilization=None),
        LordModel(id="l2", season_id="s1", role=LordRole.LORD, display_name="Sir Lancelot", married_civilization=None),
        LordModel(
            id="l3",
            season_id="s1",
            role=LordRole.LORD,
            display_name="Lady Morgane",
            married_civilization="francs",
        ),
    )
    card = kingdom_card(kingdom, lords, territory_count=5, tech_points=6)
    assert card.name == "Aquitaine"
    assert card.king == "Drasah"
    assert card.lords == ("Sir Lancelot", "Lady Morgane")
    assert card.marriages == 1
    assert not card.is_gaia


def test_kingdom_card_flags_gaia() -> None:
    """A Gaïa kingdom projects with the gaia flag set."""
    kingdom = _kingdom(kingdom_id="k-g", name="Gaïa", kind=KingdomType.GAIA)
    card = kingdom_card(kingdom, (), territory_count=8, tech_points=0)
    assert card.is_gaia


def test_territory_lines_map_owners_by_id() -> None:
    """The cadastre lines carry the owner kingdom display name."""
    aquitaine = _kingdom()
    gaia = _kingdom(kingdom_id="k-g", name="Gaïa", kind=KingdomType.GAIA)
    drawn = datetime(2026, 10, 2, tzinfo=UTC)
    territories = (
        TerritoryModel(id="t1", season_id="s1", map_key="arabie", owner_kingdom_id="k-a", drawn_at=drawn),
        TerritoryModel(id="t2", season_id="s1", map_key="oasis", owner_kingdom_id="k-g", drawn_at=drawn),
    )
    lines = territory_lines(territories, {"k-a": aquitaine, "k-g": gaia})
    assert [(line.map_name, line.owner_name) for line in lines] == [
        ("arabie", "Aquitaine"),
        ("oasis", "Gaïa"),
    ]


def test_territory_lines_survive_a_dangling_owner() -> None:
    """A dangling owner id maps to the empty label, never a crash."""
    drawn = datetime(2026, 10, 2, tzinfo=UTC)
    territories = (TerritoryModel(id="t1", season_id="s1", map_key="arabie", owner_kingdom_id="k-x", drawn_at=drawn),)
    lines = territory_lines(territories, {"k-a": _kingdom()})
    assert lines[0].owner_name == ""


def test_season_label_reads_the_config_age() -> None:
    """The label reads the current cycle and the config age display name."""
    label = season_label(_season(), default_season_config(), locale="fr")
    assert label == "Saison — cycle 2/4 — Âge féodal"
    assert season_label(_season(), default_season_config(), locale="en") == (
        "Season — cycle 2/4 — Âge féodal"
    )


def test_season_label_falls_back_on_an_unknown_age() -> None:
    """A config changed mid-season falls back to the raw age key."""
    season = _season()
    object.__setattr__(season, "current_age_key", "unknown_age")
    assert "unknown_age" in season_label(season, default_season_config(), locale="fr")


def test_tech_points_of_reads_zero_when_missing() -> None:
    """A kingdom without a technology state reads as zero points."""
    assert tech_points_of(None) == 0
    state = TechnologyState(kingdom_id="k-a", season_id="s1", tech_points=3)
    assert tech_points_of(state) == 3
