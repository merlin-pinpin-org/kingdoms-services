"""Platform types enumeration. Implemented in kingdoms-services#7."""

from __future__ import annotations

from enum import StrEnum


class PlatformType(StrEnum):
    """Supported chat platforms."""

    DISCORD = "discord"
    TWITCH = "twitch"
