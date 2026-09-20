"""User model: platform identity, registration data, per-game profiles.

GameProfile is the association between a user and a game. Game-specific
metrics (ELO rating, rank, ...) are mod/game data: they live in the mod's
own collections, keyed by this profile, not as core model fields.

Reference: kingdoms-services#4.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class GameProfile(BaseModel):
    """A user's association with a game.

    Core fields describe the association only (identity in the game, when
    it started); game-specific data belongs to the owning mod.
    """

    model_config = ConfigDict(strict=True)

    game_id: str
    in_game_name: str | None = None
    joined_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class UserModel(BaseModel):
    """Platform user with registration data and per-game profiles."""

    model_config = ConfigDict(strict=True)

    id: str = Field(alias="_id")
    platform: str
    platform_user_id: str
    display_name: str
    locale: str = "en"
    registered_at: str | None = None
    game_profiles: dict[str, GameProfile] = Field(default_factory=dict)

    def to_mongo(self) -> dict[str, Any]:
        """Convert to a MongoDB document (``_id`` is the document key)."""
        return self.model_dump(by_alias=True)

    @classmethod
    def from_mongo(cls, data: dict[str, Any]) -> UserModel:
        """Build from a MongoDB document."""
        return cls.model_validate(data)
