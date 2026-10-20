"""clans mod — entrypoint.

Lifecycle hooks (all optional, all idempotent — see
kingdoms.core.services.mod_entrypoint): the core calls ``register`` at
bot build time, ``setup_hook`` after a restart to re-register persistent
UI, ``close`` on shutdown. Hooks degrade quietly when their stack
(Mongo, Redis, providers) is not configured.

Reference example (mod-intake skill, kingdoms-services#267): the clans
mod shows a permanent mod with mutual-agreement admissions — the
business logic in :mod:`.service` runs against the core seams only.
"""

from __future__ import annotations

import logging
from typing import Any

import discord

from kingdoms.discord.ui import BLURPLE, Container, Text, UILayout

logger = logging.getLogger("kingdoms.mods.clans")


def _clan_service(bot: Any) -> Any | None:
    """Return the wired ClanService; None when the stack is down."""
    return getattr(bot, "clans_service", None)


def register(bot: Any, config: Any) -> None:
    """Wire the mod onto the bot: commands, views, admin sections."""

    @discord.app_commands.command(
        name="clan",
        description="The clans dashboard — every clan, its members, join",
    )
    @discord.app_commands.guild_only()
    async def clan_command(interaction: discord.Interaction) -> None:
        """Answer /clan with the clans dashboard (ephemeral).

        Seeing the command never implies being allowed to use it
        (AGENTS.md, runtime permission mandate): the click validates
        the guild's granted access at click time and denies ephemerally.
        """
        from kingdoms.discord.wiring import build_guild_access_service

        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        access = build_guild_access_service()
        if access is not None and guild_id:
            try:
                granted = await access.enabled_mods(guild_id)
            except Exception:
                granted = ()
            if "clans" not in granted:
                await interaction.response.send_message(
                    "Ce mod n'est pas actif sur ce serveur — un admin peut le demander "
                    "depuis le panneau d'administration.",
                    ephemeral=True,
                )
                return
        service = _clan_service(bot)
        rows = await service.dashboard(guild_id) if service and guild_id else []
        lines = [f"# \U0001f3d8 Clans ({len(rows)})"]
        for row in rows[:20]:
            lines.append(f"• **{row['name']}** — {row['member_count']} membres")
        if not rows:
            lines.append("_Aucun clan pour l'instant — le premier est à créer._")
        view = UILayout().add(
            Container(accent=BLURPLE).add(Text("\n".join(lines)))
        ).build()
        await interaction.response.send_message(view=view, ephemeral=True)

    bot.tree.add_command(clan_command)


def setup_hook(bot: Any) -> None:
    """Re-register persistent UI after a restart (dynamic items, views)."""


def close(bot: Any) -> None:
    """Stop the mod's background tasks (best-effort)."""
