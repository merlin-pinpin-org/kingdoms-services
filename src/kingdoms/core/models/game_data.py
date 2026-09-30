"""Game catalog models: maps, civs, rules, map pools and map packs.

Generic and game-agnostic (kingdoms-services#131): every entry is keyed by
``game_key`` with stable IDs, so rotating a pool or adding a civ never
breaks in-progress seasons — matches snapshot what they need. Deletes are
archival-only (``archived_at``), per the JeanJack V2.0 reference §2.
"""

from __future__ import annotations

from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field


class CatalogEntryModel(BaseModel):
    """Base shape shared by every game catalog entry."""

    model_config = ConfigDict(strict=True)

    id: str = Field(alias="_id")
    game_key: str
    name: str
    description: str = ""
    resource_url: str = ""
    archived_at: int | None = None

    def to_mongo(self) -> dict[str, Any]:
        """Convert to a MongoDB document (``_id`` is the document key)."""
        return self.model_dump(by_alias=True)

    @classmethod
    def from_mongo(cls, data: dict[str, Any]) -> Self:
        """Build from a MongoDB document."""
        return cls.model_validate(data)


class MapModel(CatalogEntryModel):
    """A map entry: ``filename`` is the opaque in-game reference (required)."""

    filename: str = ""


class CivModel(CatalogEntryModel):
    """A civilization/faction entry, curated for admin editing."""

    faction_key: str = ""


class RuleModel(CatalogEntryModel):
    """A game rule/format entry (team sizes, settings, victory conditions)."""

    rule_key: str = ""
    params: dict[str, str] = Field(default_factory=dict)


class MapPackModel(BaseModel):
    """A named bundle of maps, usable as a building block for pools."""

    model_config = ConfigDict(strict=True)

    id: str = Field(alias="_id")
    game_key: str
    name: str
    description: str = ""
    map_ids: tuple[str, ...] = ()
    archived_at: int | None = None

    def to_mongo(self) -> dict[str, Any]:
        """Convert to a MongoDB document."""
        return self.model_dump(by_alias=True)

    @classmethod
    def from_mongo(cls, data: dict[str, Any]) -> MapPackModel:
        """Build from a MongoDB document."""
        return cls.model_validate(data)


class MapPoolModel(BaseModel):
    """A selection into the catalog: which maps are in play for a rotation.

    Pools may reference individual maps and/or map packs; resolution is
    the union. Lifecycle per reference §4: compose, duplicate, activate
    (transactional), archive (never the active pool).
    """

    model_config = ConfigDict(strict=True)

    id: str = Field(alias="_id")
    game_key: str
    name: str
    description: str = ""
    map_ids: tuple[str, ...] = ()
    map_pack_ids: tuple[str, ...] = ()
    archived_at: int | None = None

    def to_mongo(self) -> dict[str, Any]:
        """Convert to a MongoDB document."""
        return self.model_dump(by_alias=True)

    @classmethod
    def from_mongo(cls, data: dict[str, Any]) -> MapPoolModel:
        """Build from a MongoDB document."""
        return cls.model_validate(data)


class MapPoolActivationModel(BaseModel):
    """One map-pool activation on a ladder, forming the pool history."""

    model_config = ConfigDict(strict=True)

    id: str = Field(alias="_id")
    ladder_id: str
    map_pool_id: str
    activated_at: int
    deactivated_at: int | None = None

    def to_mongo(self) -> dict[str, Any]:
        """Convert to a MongoDB document."""
        return self.model_dump(by_alias=True)

    @classmethod
    def from_mongo(cls, data: dict[str, Any]) -> MapPoolActivationModel:
        """Build from a MongoDB document."""
        return cls.model_validate(data)
