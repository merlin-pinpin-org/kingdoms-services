"""The generic membership commands: register/unregister for every mod.

One command factory serves every mod's membership (the ladder and any
seasonal or non-seasonal mod): the mod provides its wired
``MembershipService`` and its command group, the factory adds the
``register``/``unregister`` subcommands with the same answers
everywhere — no per-mod copy of the wiring.
"""
from __future__ import annotations

import logging
from typing import Any

import discord
from discord import app_commands

logger = logging.getLogger("kingdoms.membership")


def register_membership_commands(
    group: app_commands.Group, membership: Any
) -> None:
    """Add the register/unregister subcommands to one mod's group.

    ``membership`` is the mod's wired core MembershipService; the
    group is an ``app_commands.Group`` owned by the mod's surface.
    """

    @group.command(name="register")
    async def register_command(interaction: discord.Interaction) -> None:
        """Register on the mod (role synced)."""
        guild_id = str(interaction.guild_id) if interaction.guild_id is not None else ""
        user_id = str(interaction.user.id)
        result = await membership.register(guild_id, user_id, interaction.user.display_name)
        if result.ok:
            await interaction.response.send_message(f"Inscrit ({result.summary}).", ephemeral=True)
        else:
            await interaction.response.send_message(result.summary, ephemeral=True)

    @group.command(name="unregister")
    async def unregister_command(interaction: discord.Interaction) -> None:
        """Unregister from the mod (role synced)."""
        guild_id = str(interaction.guild_id) if interaction.guild_id is not None else ""
        user_id = str(interaction.user.id)
        result = await membership.unregister(guild_id, user_id)
        if result.ok:
            await interaction.response.send_message(result.summary, ephemeral=True)
        else:
            await interaction.response.send_message(result.summary, ephemeral=True)
