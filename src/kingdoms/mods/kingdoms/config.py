"""Kingdoms mod season configuration.

Season configuration is **data**: every tunable of reference §27 lives in
``config/kingdoms/season.yaml`` (or the settings section of the mod
declaration) — never hardcoded. Defaults here are the Season II values
from the reference document; the admin overrides them without touching
code (decisions D9 — technology costs parametrizable — and D36).

The config is deliberately plain (Pydantic, strict): it is validated once
at load and shared by the domain services of later slices.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

SEASON_CONFIG_FILENAME = "season.yaml"


class NameRules(BaseModel):
    """Kingdom name rules — length and characters, admin-tunable (D21)."""

    model_config = ConfigDict(strict=True)

    min_length: int = Field(default=2, ge=1)
    max_length: int = Field(default=32, ge=1)
    pattern: str = r"^[\w\s'\-]+$"


class Epoch(BaseModel):
    """One age of the season (reference §15, decision D18)."""

    model_config = ConfigDict(strict=True)

    key: str
    display_name: str
    gaia_ai_level: int = Field(ge=1, le=5)
    tech_points: int = Field(ge=0)
    extra_marriages: int = Field(ge=0)


class TechnologyCosts(BaseModel):
    """Technology costs and purchase limits.

    Costs (tech points) and per-season purchase limits of the ten
    special actions (reference §20, decisions D9/D36).

    ``limit`` is the maximum number of purchases per season (0 = no
    limit beyond affordability).
    """

    model_config = ConfigDict(strict=True)

    embuscade: int = Field(default=2, ge=0)
    traquenard: int = Field(default=1, ge=0)
    patrouille: int = Field(default=2, ge=0)
    contre_espionnage: int = Field(default=2, ge=0)
    sabotage: int = Field(default=1, ge=0)
    explorateur: int = Field(default=2, ge=0)
    jeu_d_armes: int = Field(default=1, ge=0)
    mariage_arrange: int = Field(default=3, ge=0)
    corruption: int = Field(default=4, ge=0)
    garde_royale: int = Field(default=1, ge=0)


class AttackSettings(BaseModel):
    """Attack/defense budgets and delays (reference §11-§13)."""

    model_config = ConfigDict(strict=True)

    attacks_per_week: int = Field(default=1, ge=0)
    defenses_per_week: int = Field(default=1, ge=0)
    player_attack_delay_hours: int = Field(default=6, ge=0)
    gaia_attack_delay_hours: int = Field(default=3, ge=0)
    gaia_attack_max_per_kingdom: int = Field(default=1, ge=1)
    gaia_attack_max_total: int = Field(default=7, ge=1)


class EventSettings(BaseModel):
    """Weekly event schedules (reference §16-§17, decision D1).

    Cron expressions keep the admin free to move every event without a
    code change; the defaults are the Season II values.
    """

    model_config = ConfigDict(strict=True)

    cycle_cron: str = "30 23 * * SUN"
    age_cron: str = "0 0 * * WED"
    exploration_cron: str = "0 14 * * SAT"
    lords_day_new_maps: int = Field(default=8, ge=0)


class KingdomsSeasonConfig(BaseModel):
    """Full season configuration.

    Reference §27: configuration data, persisted across seasons and
    modifiable by the admin.
    """

    model_config = ConfigDict(strict=True)

    weeks: int = Field(default=4, ge=1)
    kingdoms_count: int = Field(default=2, ge=1)
    lords_per_kingdom: int = Field(default=4, ge=1)
    territories_per_kingdom: int = Field(default=5, ge=0)
    gaia_territories: int = Field(default=8, ge=0)
    starting_civilizations: int = Field(default=0, ge=0)
    garrison_enabled: bool = False
    ages: tuple[Epoch, ...] = ()
    names: NameRules = Field(default_factory=NameRules)
    technologies: TechnologyCosts = Field(default_factory=TechnologyCosts)
    attacks: AttackSettings = Field(default_factory=AttackSettings)
    events: EventSettings = Field(default_factory=EventSettings)


def default_season_config() -> KingdomsSeasonConfig:
    """Return the Season II default configuration (reference §15, D18)."""
    return KingdomsSeasonConfig(
        ages=(
            Epoch(key="dark_age", display_name="Âge sombre", gaia_ai_level=2, tech_points=0, extra_marriages=1),
            Epoch(key="feudal_age", display_name="Âge féodal", gaia_ai_level=3, tech_points=1, extra_marriages=1),
            Epoch(key="castle_age", display_name="Âge des châteaux", gaia_ai_level=5, tech_points=2, extra_marriages=1),
            Epoch(key="imperial_age", display_name="Âge impérial", gaia_ai_level=5, tech_points=2, extra_marriages=1),
        ),
    )


def load_season_config(config_dir: Path) -> KingdomsSeasonConfig:
    """Load the season configuration from ``<config_dir>/kingdoms/season.yaml``.

    Missing file or empty mapping → Season II defaults (the game must be
    playable with no local overrides). An invalid override raises: a
    broken config must fail loudly, never load half-validated.
    """
    season_file = config_dir / "kingdoms" / SEASON_CONFIG_FILENAME
    if not season_file.is_file():
        return default_season_config()
    with open(season_file, encoding="utf-8") as fh:
        data: Any = yaml.safe_load(fh)
    if data is None:
        return default_season_config()
    if not isinstance(data, dict):
        raise ValueError(f"{season_file}: season config must be a mapping")
    return KingdomsSeasonConfig.model_validate(data)
