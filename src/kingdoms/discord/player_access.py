"""Root-channel player gating (permissions-serveur-v1, Drasah 19/10).

A newcomer sees only Conscription and Beta — the root managed salons
(🏛-kingdoms-home, 📡-live-dashboard) are masked for @everyone. The live
dashboard is unlocked by the player roles (Kingdoms Lord / Kingdoms King);
home-admin-v1 (Drasah 28/10): the home channel is ADMIN-ONLY. The bot keeps
its explicit allow (lesson Check 75: it is no admin) so its panels keep
deploying.
"""

from __future__ import annotations

import logging
from typing import Any

import discord

logger = logging.getLogger("kingdoms.player_access")

PLAYER_ROLE_NAMES: tuple[str, ...] = ("Kingdoms Lord", "Kingdoms King", "Kingdoms Admin")

ADMIN_ROLE_NAMES: tuple[str, ...] = ("Kingdoms Admin",)
"""home-admin-v1 (Drasah 28/10, live report Rakussy): the 🏛-kingdoms-home
channel is ADMIN-ONLY — the player roles no longer unlock it (a freshly
validated Lord saw the staff front door). Only Kingdoms Admin (+ the bot)
keeps the view; the live dashboard keeps the player gating."""


async def apply_player_gating(
    guild: discord.Guild,
    channel: Any,
    role_names: tuple[str, ...] = PLAYER_ROLE_NAMES,
) -> None:
    """Mask one root channel for @everyone, unlock the given roles (best effort).

    Rate-limit lesson (Checks 89/92/101, livelock 24/10): this gating
    is re-applied on EVERY resolve and the live dashboard refreshes
    every 15 s — per-target ``set_permissions`` calls rate-limit-storm
    Discord (per-channel overwrite throttle) and freeze the whole bot
    HTTP layer. The desired overwrites are therefore computed in
    memory, skipped entirely when the live channel already matches
    (the steady-state refresh costs ZERO API writes), and applied
    through ONE ``channel.edit(overwrites=...)`` otherwise. Foreign
    overwrites on the channel are preserved.
    """
    desired: dict[Any, discord.PermissionOverwrite] = {}
    everyone = getattr(guild, "default_role", None)
    if everyone is not None:
        desired[everyone] = discord.PermissionOverwrite(view_channel=False)
    for role_name in role_names:
        role = discord.utils.get(getattr(guild, "roles", []), name=role_name)
        if role is not None:
            desired[role] = discord.PermissionOverwrite(view_channel=True, read_message_history=True)
    me = getattr(guild, "me", None)
    if me is not None:
        desired[me] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True, manage_messages=True
        )
    if not desired:
        return
    live = dict(getattr(channel, "overwrites", None) or {})
    # home-admin-v1: a player role that no longer unlocks this channel
    # (e.g. Lord/King overwrites left by the previous policy on the
    # home channel) is STALE — it is dropped from the merged map, not
    # merely ignored, otherwise the old allow would keep leaking.
    stale = [
        target
        for target in live
        if getattr(target, "name", None) in PLAYER_ROLE_NAMES and target not in desired
    ]
    if not stale and all(live.get(target) == overwrite for target, overwrite in desired.items()):
        return  # converged — zero API write (the 15 s refresh stays free)
    merged = {target: overwrite for target, overwrite in live.items() if target not in stale}
    merged.update(desired)
    await channel.edit(overwrites=merged, reason="kingdoms: player gating (permissions-serveur-v1)")
