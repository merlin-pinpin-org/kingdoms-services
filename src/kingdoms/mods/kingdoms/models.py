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

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

GAIA_KINGDOM_KEY = "gaia"


def _from_wire[ModelT: BaseModel](cls: type[ModelT], data: dict[str, Any]) -> ModelT:
    """Rebuild a model from a MongoDB document (wire-tolerant).

    MongoDB gives back plain strings for the StrEnum fields and lists
    for the tuple/list fields; the models are ``strict=True`` so the
    in-code constructor calls stay exact, but ``from_mongo`` must read
    the wire leniently (``strict=False``) or every stored document
    fails validation on the way back (first seen live: the starting
    draft re-reading a freshly upserted kingdom, 2026-10-10).
    """
    return TypeAdapter(cls).validate_python(data, strict=False)




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
    civilizations: list[str] = Field(default_factory=list)
    secured_civilizations: list[str] = Field(default_factory=list)
    tech_points_bank: int = Field(default=0, ge=0)

    def to_mongo(self) -> dict[str, Any]:
        """Convert to a MongoDB document."""
        return self.model_dump(by_alias=True)

    @classmethod
    def from_mongo(cls, data: dict[str, Any]) -> KingdomModel:
        """Build from a MongoDB document."""
        return _from_wire(cls, data)

    @property
    def is_gaia(self) -> bool:
        """Gaïa kingdoms are AI-controlled and never enrollable."""
        return self.type == KingdomType.GAIA


class LordModel(BaseModel):
    """A player of the current season: king or lord of a kingdom.

    ``married_civilization`` is the marriage target (one per lord,
    D34); ``marriage_locked_until`` is the real-time combat lock of a
    fresh marriage (D59: 24h standard, D60: 6h arranged) - a locked
    lord can neither attack nor defend while the unix timestamp is in
    the future.

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
    marriage_locked_until: int | None = None
    left: bool = False
    left_reason: str | None = None

    def to_mongo(self) -> dict[str, Any]:
        """Convert to a MongoDB document."""
        return self.model_dump(by_alias=True)

    @classmethod
    def from_mongo(cls, data: dict[str, Any]) -> LordModel:
        """Build from a MongoDB document."""
        return _from_wire(cls, data)


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
    protected_until: datetime | None = None
    """Anti-attack/anti-corruption shield (D15/D37/D47): Garde Royale
    and Corruption protect a territory until the recorded instant."""

    def to_mongo(self) -> dict[str, Any]:
        """Convert to a MongoDB document."""
        return self.model_dump(by_alias=True)

    @classmethod
    def from_mongo(cls, data: dict[str, Any]) -> TerritoryModel:
        """Build from a MongoDB document."""
        return _from_wire(cls, data)

    def is_protected_at(self, now: datetime) -> bool:
        """Whether the anti-attack/anti-corruption shield is active (D15/D37/D47)."""
        if self.protected_until is None:
            return False
        until = self.protected_until
        if until.tzinfo is None:
            until = until.replace(tzinfo=UTC)
        return until > now


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
        return _from_wire(cls, data)

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


class AttackKind(StrEnum):
    """A kingdom-vs-kingdom attack or a Gaïa free-for-all (D6)."""

    PLAYER = "player"
    GAIA = "gaia"


class AttackState(StrEnum):
    """State machine of an attack (reference §12).

    ``declared`` waits for a defender until the delay expires;
    ``defended`` waits for the game result; ``expired`` applied the
    admin-configured no-defense outcome; ``resolved`` carries the final
    winner (capture or conservation) and never moves again.
    """

    DECLARED = "declared"
    DEFENDED = "defended"
    EXPIRED = "expired"
    RESOLVED = "resolved"


class AttackModel(BaseModel):
    """An attack of the current season on one territory (§11-§13).

    Player attacks are 1v1 without reinforcements (D41) and never share
    a target with another ongoing attack (D42). Gaïa attacks are a
    free-for-all: the first declarer reserves the territory and the
    slot (§13.2), one lord per kingdom, up to the configured maximum
    participants (D6/D42).

    ``effects`` carries the combat-technology effects engaged on this
    attack (sabotaged civilizations, counter-espionage…), keyed by
    technology name — data for the surface and the game setup.
    """

    model_config = ConfigDict(strict=True)

    id: str = Field(alias="_id")
    season_id: str
    kind: AttackKind
    state: AttackState = AttackState.DECLARED
    territory_id: str
    map_key: str
    defender_kingdom_id: str
    attacker_lord_id: str
    attacker_kingdom_id: str
    lobby_url: str = ""
    declared_at: datetime
    expires_at: datetime
    defender_lord_id: str | None = None
    resolved_at: datetime | None = None
    winner_kingdom_id: str | None = None
    restituted: bool = False
    participants: list[str] = Field(default_factory=list)
    effects: dict[str, Any] = Field(default_factory=dict)

    def to_mongo(self) -> dict[str, Any]:
        """Convert to a MongoDB document."""
        return self.model_dump(by_alias=True)

    @classmethod
    def from_mongo(cls, data: dict[str, Any]) -> AttackModel:
        """Build from a MongoDB document."""
        return _from_wire(cls, data)

    @property
    def is_over(self) -> bool:
        """Whether the attack reached a final state."""
        return self.state in (AttackState.RESOLVED, AttackState.EXPIRED)


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
    finished: bool = False
    winner_kingdom_id: str | None = None
    pending_marriage_losses: list[str] = Field(default_factory=list)
    """Kingdom ids that lost a combat since the last cycle switch: their
    lords' marriages drop at the next recalculation (D34/D53)."""

    def to_mongo(self) -> dict[str, Any]:
        """Convert to a MongoDB document."""
        return self.model_dump(by_alias=True)

    @classmethod
    def from_mongo(cls, data: dict[str, Any]) -> SeasonState:
        """Build from a MongoDB document."""
        return _from_wire(cls, data)


class DuelModel(BaseModel):
    """One ShowMatch PA2 duel (D50): a 1v1 between two lords.

    Maps come from the duelist's own territories without reuse, civs
    never repeat, and the Megarandom fallback picks a random civ when
    the pool is empty. ``players`` holds the team members of each side
    once the format escalates to 2v2/3v3.
    """

    model_config = ConfigDict(strict=True)

    index: int
    format: str = "1v1"
    side_a: list[str] = Field(default_factory=list)
    side_b: list[str] = Field(default_factory=list)
    map_key: str | None = None
    civilizations: dict[str, str] = Field(default_factory=dict)
    winner_side: str | None = None

    def to_mongo(self) -> dict[str, Any]:
        """Convert to a MongoDB document."""
        return self.model_dump(by_alias=True)

    @classmethod
    def from_mongo(cls, data: dict[str, Any]) -> DuelModel:
        """Build from a MongoDB document."""
        return _from_wire(cls, data)


class ShowMatchModel(BaseModel):
    """The ShowMatch PA2 deciding a tied Conquest finish (D50).

    Two kingdoms play mandatory duels until one leads 2-0 (1v1 first,
    then 2v2, 3v3... when every lord has dueled); a 1-1 tie chains a
    new duel with other lords. Maps and civs never repeat.
    """

    model_config = ConfigDict(strict=True)

    id: str = Field(alias="_id")
    season_id: str
    kingdom_a: str
    kingdom_b: str
    score_a: int = Field(default=0, ge=0)
    score_b: int = Field(default=0, ge=0)
    duels: list[DuelModel] = Field(default_factory=list)
    used_map_keys: list[str] = Field(default_factory=list)
    used_civilizations: list[str] = Field(default_factory=list)
    finished: bool = False
    winner_kingdom_id: str | None = None

    def to_mongo(self) -> dict[str, Any]:
        """Convert to a MongoDB document."""
        return self.model_dump(by_alias=True)

    @classmethod
    def from_mongo(cls, data: dict[str, Any]) -> ShowMatchModel:
        """Build from a MongoDB document."""
        return _from_wire(cls, data)
