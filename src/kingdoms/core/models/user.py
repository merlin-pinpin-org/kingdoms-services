"""User model: platform identity, registration data, per-game profiles.

Reference: kingdoms-services#4.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class GameProfile(BaseModel):
    """Per-game player profile; game-specific fields live in `data`."""

    game_id: str
    data: dict[str, Any] = Field(default_factory=dict)


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
