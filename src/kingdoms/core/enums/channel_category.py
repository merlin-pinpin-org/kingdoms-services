"""Channel categories: routing keys used by ChannelService.

Reference: ADR-0003 (channel categories). Extended in kingdoms-services#7.
"""

from __future__ import annotations

from enum import StrEnum


class ChannelCategory(StrEnum):
    """Logical channel categories; mods ask for channels by category."""

    ADMIN = "admin"
    REPORTS = "reports"
    LADDER = "ladder"
    REGISTRATION = "registration"
