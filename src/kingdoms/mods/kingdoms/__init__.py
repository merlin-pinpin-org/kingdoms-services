"""Kingdoms mod (Season II territory-conquest AoE2) — foundations.

Slice T1 (kingdoms-services#156): season configuration (data, never
hardcoded — reference §27/§28, decisions D9/D36), season-state models
(Kingdom/Lord/Territory, Gaïa as an AI kingdom — D32) and the
config-vs-season data boundary: launching a new season resets every
season datum, the configuration persists (§3.3, D38/D52).

Design source of truth: kingdoms repo docs/MODS/kingdoms/ (reference
Season II + DECISIONS.md D1-D54).
"""
from kingdoms.mods.kingdoms.config import (
    AttackSettings,
    Epoch,
    EventSettings,
    KingdomsSeasonConfig,
    TechnologyCosts,
    load_season_config,
)
from kingdoms.mods.kingdoms.models import (
    KingdomModel,
    KingdomType,
    LordModel,
    LordRole,
    SeasonState,
    TechnologyState,
    TerritoryModel,
)

__all__ = [
    "AttackSettings",
    "Epoch",
    "EventSettings",
    "KingdomModel",
    "KingdomType",
    "KingdomsSeasonConfig",
    "LordModel",
    "LordRole",
    "SeasonState",
    "TechnologyCosts",
    "TechnologyState",
    "TerritoryModel",
    "load_season_config",
]
