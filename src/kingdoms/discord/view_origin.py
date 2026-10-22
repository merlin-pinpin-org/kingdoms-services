"""View-origin detection: pinned panels vs command-opened ephemerals.

A view reached from a pinned panel is a dedicated ephemeral with no back
button (the pin stays under it, navigation is free). A view opened by a
slash command keeps its back buttons so the user can walk back to the
command's menu. The origin is detected from the clicked message: an
ephemeral message means a command flow, a persistent (pinned) message
means the panel flow.
"""

from __future__ import annotations

from typing import Any

import discord


def from_pin(interaction: discord.Interaction) -> bool:
    """Whether the clicked view comes from a pinned panel (not a command)."""
    message: Any = getattr(interaction, "message", None)
    if message is None:
        return False
    flags: Any = getattr(message, "flags", None)
    if flags is None:
        return False
    ephemeral = getattr(flags, "ephemeral", False)
    if callable(ephemeral):
        ephemeral = bool(ephemeral())
    return not bool(ephemeral)
