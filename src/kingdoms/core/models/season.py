"""Season models: generic, mod-agnostic seasons over a ladder (kingdoms-services#132).

A season wraps the ladder's map-pool mechanics: activating a season is a
transactional pool switch (reference §4). ``reset_ratings`` defaults to
false — the AoE2 ladder ships with rotations only; a reset is an explicit
admin demand handled by the rating path (``RESET`` rating-history entries).
"""

from __future__ import annotations

from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field

SEASON_STATE_SCHEDULED = "scheduled"
SEASON_STATE_ACTIVE = "active"
SEASON_STATE_ENDED = "ended"


class SeasonModel(BaseModel):
    """One season on a ladder: a time-boxed pool activation window."""

    model_config = ConfigDict(strict=True)

    id: str = Field(alias="_id")
    ladder_id: str
    name: str
    map_pool_id: str
    start_at: int
    end_at: int | None = None
    reset_ratings: bool = False
    state: str = SEASON_STATE_SCHEDULED
    activated_at: int | None = None
    ended_at: int | None = None

    def to_mongo(self) -> dict[str, Any]:
        """Convert to a MongoDB document (``_id`` is the document key)."""
        return self.model_dump(by_alias=True)

    @classmethod
    def from_mongo(cls, data: dict[str, Any]) -> Self:
        """Build from a MongoDB document."""
        return cls.model_validate(data)
