"""Guild model: per-server configuration (locale, channel category mapping).

Reference: kingdoms-services#4.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class GuildModel(BaseModel):
    """Per-guild configuration."""

    model_config = ConfigDict(strict=True)

    id: str = Field(alias="_id")
    platform: str
    locale: str = "en"
    channel_categories: dict[str, str] = Field(default_factory=dict)
    role_keys: dict[str, str] = Field(default_factory=dict)

    def to_mongo(self) -> dict[str, Any]:
        """Convert to a MongoDB document (``_id`` is the document key)."""
        return self.model_dump(by_alias=True)

    @classmethod
    def from_mongo(cls, data: dict[str, Any]) -> GuildModel:
        """Build from a MongoDB document."""
        return cls.model_validate(data)
