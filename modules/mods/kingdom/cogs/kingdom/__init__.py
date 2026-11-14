"""The kingdom mod's cog package (§7 cogs) — the mod's Discord layer.

Lifecycle hooks (all optional, all idempotent — see
botforge.core.services.mod_entrypoint): the core calls ``register`` at
bot build time, ``setup_hook`` after a restart to re-register persistent
UI, ``close`` on shutdown. Hooks degrade quietly when their stack
(Mongo, Redis, providers) is not configured.
"""

from __future__ import annotations

from typing import Any

import discord

from .cog import status_view


def register(bot: Any, config: Any) -> None:
    """Wire the cog onto the bot: commands, views, admin sections."""

    @discord.app_commands.command(
        name="kingdom",
        description="Kingdom — generated placeholder, replace me",
    )
    async def _cmd(interaction: discord.Interaction) -> None:
        view = await status_view(str(interaction.guild_id or ""))
        await interaction.response.send_message(view=view, ephemeral=True)

    bot.tree.add_command(_cmd)


def setup_hook(bot: Any) -> None:
    """Re-register persistent UI after a restart (dynamic items, views)."""


def close(bot: Any) -> None:
    """Stop the mod's background tasks (best-effort)."""
