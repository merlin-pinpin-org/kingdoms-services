"""Kingdoms mod — Lot C first slice: season snapshots for the Lot B views.

Pure projections: season-state models (models.py) in, view dataclasses
(views.py) out. No Discord, no stores, no side effects — the wiring of
a later slice feeds these from the gRPC seams and hands the results to
the builders; the unit tests run on plain data only.

Mapping decisions (reference §20, D9/D36): the technology shop is
derived from the config (costs and limits), the display names come from
the designer-authored FR/EN catalog below, and the cadastre lines read
the territory ``map_key`` as the map label until the map catalog lands.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from kingdoms.mods.kingdoms.config import KingdomsSeasonConfig, TechnologyCosts
from kingdoms.mods.kingdoms.models import (
    KingdomModel,
    LordModel,
    SeasonState,
    TechnologyState,
    TerritoryModel,
)
from kingdoms.mods.kingdoms.views import (
    KingdomCard,
    SpecialActionCard,
    TerritoryLine,
)

MOD_KEY = "kingdoms"

TECHNOLOGY_NAMES: dict[str, dict[str, str]] = {
    "embuscade": {"fr": "Embuscade", "en": "Ambush"},
    "traquenard": {"fr": "Traquenard", "en": "Trap"},
    "patrouille": {"fr": "Patrouille", "en": "Patrol"},
    "contre_espionnage": {"fr": "Contre-espionnage", "en": "Counter-intelligence"},
    "sabotage": {"fr": "Sabotage", "en": "Sabotage"},
    "explorateur": {"fr": "Explorateur", "en": "Explorer"},
    "jeu_d_armes": {"fr": "Jeu d'armes", "en": "Weapons play"},
    "mariage_arrange": {"fr": "Mariage arrangé", "en": "Arranged marriage"},
    "corruption": {"fr": "Corruption", "en": "Corruption"},
    "garde_royale": {"fr": "Garde royale", "en": "Royal guard"},
}

DEFAULT_LOCALE = "fr"


def _tech_name(key: str, locale: str) -> str:
    """Resolve a technology display name (French fallback, drasah pattern)."""
    return TECHNOLOGY_NAMES.get(key, {}).get(locale, TECHNOLOGY_NAMES.get(key, {}).get(DEFAULT_LOCALE, key))


def tech_shop(config: TechnologyCosts, *, locale: str = DEFAULT_LOCALE) -> tuple[SpecialActionCard, ...]:
    """Project the config technology costs into the shop cards (B4).

    Only the integer costs become cards: the ``limits`` mapping is
    admin data (D36), not a shop entry.
    """
    cards = [
        SpecialActionCard(key=key, name=_tech_name(key, locale), cost=cost)
        for key, cost in config.model_dump().items()
        if isinstance(cost, int) and cost > 0
    ]
    return tuple(cards)


def kingdom_card(
    kingdom: KingdomModel,
    lords: Sequence[LordModel],
    *,
    territory_count: int,
    tech_points: int,
    locale: str = DEFAULT_LOCALE,
) -> KingdomCard:
    """Project one kingdom and its lords into a profile card (B2)."""
    del locale  # player names are already localized display names
    king = next((lord.display_name for lord in lords if lord.role.value == "king"), "")
    subjects = tuple(lord.display_name for lord in lords if lord.role.value != "king")
    marriages = sum(1 for lord in lords if lord.married_civilization is not None)
    return KingdomCard(
        name=kingdom.name,
        king=king,
        lords=subjects,
        territories=territory_count,
        tech_points=tech_points,
        marriages=marriages,
        is_gaia=kingdom.is_gaia,
    )


def territory_lines(
    territories: Sequence[TerritoryModel],
    kingdoms_by_id: Mapping[str, KingdomModel],
) -> tuple[TerritoryLine, ...]:
    """Project the season territories into cadastre lines (B1).

    A territory whose owner is unknown (a dangling id in a mid-transfer
    read) maps to the empty owner label — the cadastre never crashes on
    a concurrent season transition.
    """
    return tuple(
        TerritoryLine(
            map_name=territory.map_key,
            owner_name=(
                kingdoms_by_id[territory.owner_kingdom_id].name
                if territory.owner_kingdom_id in kingdoms_by_id
                else ""
            ),
        )
        for territory in territories
    )


def season_label(season: SeasonState, config: KingdomsSeasonConfig, *, locale: str = DEFAULT_LOCALE) -> str:
    """Project the season progression into the cadastre sublabel (B1).

    Reads the current age display name from the config; an unknown age
    key (config changed mid-season) falls back to the raw key.
    """
    age_name = next(
        (age.display_name for age in config.ages if age.key == season.current_age_key),
        season.current_age_key,
    )
    if locale == "en":
        return f"Season — cycle {season.current_cycle}/{season.weeks} — {age_name}"
    return f"Saison — cycle {season.current_cycle}/{season.weeks} — {age_name}"


def tech_points_of(state: TechnologyState | None) -> int:
    """Read a kingdom's tech points (a missing state reads as zero)."""
    return state.tech_points if state is not None else 0
