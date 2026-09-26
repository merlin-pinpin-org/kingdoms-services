"""Channel categories: routing keys used by ChannelService.

Core defines only platform-level categories. Mods declare their own
categories via their mod definitions (kingdoms-services#26); the core never
enumerates mod-specific channels. Reference: ADR-0003.
"""

from __future__ import annotations

from enum import StrEnum


class ChannelCategory(StrEnum):
    """Platform-level channel categories; mods add their own per mod."""

    ADMIN = "admin"
    REPORTS = "reports"
    LOGS = "logs"
    BOT_LOGS = "bot_logs"
