"""Channel model: channel registry keyed by ChannelCategory.

Reference: kingdoms-services#4.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ChannelModel(BaseModel):
    """Channel registry entry."""

    model_config = ConfigDict(strict=True)

    id: str = Field(alias="_id")
    guild_id: str
    platform: str
    category: str
    channel_id: str
    name: str
