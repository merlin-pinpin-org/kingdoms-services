"""Kingdoms mod season-state models.

Season data is **reset wholesale on every new season launch** (reference
§3.3/§28, decision D38/D52): these models are the per-season state the
bot manages. They never contain Discord or game IDs — identities are
platform-level concerns; the mod stores cross-platform entity keys and
resolves surfaces through the platform services.

Placement (ADR-0020): the domain core owns these collections in
svc-core; the Discord surface reads them over the gRPC seams.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

GAIA_KINGDOM_KEY = "gaia"


class KingdomType(StrEnum):
    """Player kingdom vs the AI kingdom (Gaïa — D32)."""

    PLAYER = "player"
    GAIA = "gaia"


class LordRole(StrEnum):
    """King or Lord (reference §5)."""

    KING = "king"
    LORD = "lord"


class KingdomModel(BaseModel):
    """A kingdom of the current season (player or Gaïa)."""

    model_config = ConfigDict(strict=True)

    id: str = Field(alias="_id")
    season_id: str
    type: KingdomType
    name: str
    marriage_capacity: int = Field(default=0, ge=0)
    name_approved: bool = True

    def to_mongo(self) -> dict[str, Any]:
        """Convert to a MongoDB document."""
        return self.model_dump(by_alias=True)

    @classmethod
    def from_mongo(cls, data: dict[str, Any]) -> KingdomModel:
        """Build from a MongoDB document."""
        return cls.model_validate(data)

    @property
    def is_gaia(self) -> bool:
        """Gaïa kingdoms are AI-controlled and never enrollable."""
        return self.type == KingdomType.GAIA


class LordModel(BaseModel):
    """A player of the current season: king or lord of a kingdom.

    ``attack_used``/``defense_used`` are the weekly budgets consumed in
    the current cycle (1+1 per week — reference §11, recharge at the
    cycle switch, D1).
    """

    model_config = ConfigDict(strict=True)

    id: str = Field(alias="_id")
    season_id: str
    kingdom_id: str | None = None
    role: LordRole
    display_name: str
    in_queue: bool = False
    attack_used: int = Field(default=0, ge=0)
    defense_used: int = Field(default=0, ge=0)
    married_civilization: str | None = None
    left: bool = False
    left_reason: str | None = None

    def to_mongo(self) -> dict[str, Any]:
        """Convert to a MongoDB document."""
        return self.model_dump(by_alias=True)

    @classmethod
    def from_mongo(cls, data: dict[str, Any]) -> LordModel:
        """Build from a MongoDB document."""
        return cls.model_validate(data)


class TerritoryModel(BaseModel):
    """A territory of the current season: one AoE2 map with an owner.

    A map drawn once is "out" for the whole season (reference §8);
    ownership changes through the idempotent transfer primitive.
    """

    model_config = ConfigDict(strict=True)

    id: str = Field(alias="_id")
    season_id: str
    map_key: str
    owner_kingdom_id: str
    drawn_at: datetime

    def to_mongo(self) -> dict[str, Any]:
        """Convert to a MongoDB document."""
        return self.model_dump(by_alias=True)

    @classmethod
    def from_mongo(cls, data: dict[str, Any]) -> TerritoryModel:
        """Build from a MongoDB document."""
        return cls.model_validate(data)


class TechnologyState(BaseModel):
    """Technology state of a kingdom.

    Points and per-technology purchase counters for the current season
    (decisions D9/D36).
    """

    model_config = ConfigDict(strict=True)

    kingdom_id: str = Field(alias="_id")
    season_id: str
    tech_points: int = Field(default=0, ge=0)
    purchases: dict[str, int] = Field(default_factory=dict)

    def to_mongo(self) -> dict[str, Any]:
        """Convert to a MongoDB document."""
        return self.model_dump(by_alias=True)

    @classmethod
    def from_mongo(cls, data: dict[str, Any]) -> TechnologyState:
        """Build from a MongoDB document."""
        return cls.model_validate(data)

    def can_afford(self, cost: int) -> bool:
        """Whether the kingdom can spend ``cost`` tech points."""
        return self.tech_points >= cost

    def spend(self, technology: str, cost: int, limit: int) -> bool:
        """Spend points on a technology, enforcing its purchase limit.

        Idempotency is the caller's concern (state transitions); this
        enforces the economic invariants only: affordability and the
        per-season limit (D36). A ``limit`` of 0 means unlimited.
        """
        if not self.can_afford(cost):
            return False
        bought = self.purchases.get(technology, 0)
        if limit > 0 and bought >= limit:
            return False
        self.tech_points -= cost
        self.purchases[technology] = bought + 1
        return True


class SeasonState(BaseModel):
    """The current season: its schedule and progression (D1, D38, D52).

    ``current_cycle`` counts completed cycles; ``current_age_key`` is
    the age the season is in (indexes the config ages).
    """

    model_config = ConfigDict(strict=True)

    id: str = Field(alias="_id")
    started_at: datetime
    weeks: int = Field(ge=1)
    current_cycle: int = Field(default=0, ge=0)
    current_age_key: str
    imposed_kingdoms: bool = False
    out_maps: list[str] = Field(default_factory=list)

    def to_mongo(self) -> dict[str, Any]:
        """Convert to a MongoDB document."""
        return self.model_dump(by_alias=True)

    @classmethod
    def from_mongo(cls, data: dict[str, Any]) -> SeasonState:
        """Build from a MongoDB document."""
        return cls.model_validate(data)
