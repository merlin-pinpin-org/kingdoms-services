"""Root-channel player gating (permissions-serveur-v1, Drasah 19/10).

A newcomer sees only Conscription and Beta — the root managed salons
(🏛-kingdoms-home, 📡-live-dashboard) are masked for @everyone and
unlocked by the player roles (Kingdoms Lord / Kingdoms King), granted
at acceptance. The bot keeps its explicit allow (lesson Check 75: it
is no admin) so its panels keep deploying.
"""

from __future__ import annotations

import logging
from typing import Any

import discord

logger = logging.getLogger("kingdoms.player_access")

PLAYER_ROLE_NAMES: tuple[str, ...] = ("Kingdoms Lord", "Kingdoms King", "Kingdoms Admin")


async def apply_player_gating(guild: discord.Guild, channel: Any) -> None:
    """Mask one root channel for @everyone, unlock the player roles (best effort).

    Idempotent: the overwrites are re-applied on every resolve, so an
    adopted channel converges at the next startup.
    """
    everyone = getattr(guild, "default_role", None)
    if everyone is not None:
        await channel.set_permissions(
            everyone,
            overwrite=discord.PermissionOverwrite(view_channel=False),
            reason="kingdoms: player gating (permissions-serveur-v1)",
        )
    for role_name in PLAYER_ROLE_NAMES:
        role = discord.utils.get(getattr(guild, "roles", []), name=role_name)
        if role is not None:
            await channel.set_permissions(
                role,
                overwrite=discord.PermissionOverwrite(view_channel=True, read_message_history=True),
                reason="kingdoms: player gating role access",
            )
    me = getattr(guild, "me", None)
    if me is not None:
        await channel.set_permissions(
            me,
            overwrite=discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True, manage_messages=True
            ),
            reason="kingdoms: player gating bot access",
        )
