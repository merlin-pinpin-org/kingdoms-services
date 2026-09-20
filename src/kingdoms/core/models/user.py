"""User model: platform identity, registration data, per-game profiles.

Reference: kingdoms-services#4.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class GameProfile(BaseModel):
    """Per-game player profile."""

    game_id: str
    in_game_name: str | None = None
    rating: int = 1000


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
