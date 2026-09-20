"""Guild model: per-server configuration (locale, channel category mapping).

Reference: kingdoms-services#4.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class GuildModel(BaseModel):
    """Per-guild configuration."""

    model_config = ConfigDict(strict=True)

    id: str = Field(alias="_id")
    platform: str
    locale: str = "en"
    channel_categories: dict[str, str] = Field(default_factory=dict)
    role_keys: dict[str, str] = Field(default_factory=dict)
