"""The ladder's home view: the mod's front door inside the guild home.

The core home routes ``mod:ladder`` here; the mod decides what its
home shows. The ladder's home answers with the mod's commands and the
**Nous rejoindre** button — the staff application entry point, a
persistent ``staff:apply:ladder`` button served by the staff surface.
"""
from __future__ import annotations

import discord

from kingdoms.discord.staff import StaffApplyButton


async def build_ladder_home_view(interaction: discord.Interaction) -> None:
    """Answer the home's mod:ladder click with the ladder home (ephemeral)."""
    view = discord.ui.LayoutView(timeout=None)
    row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    row.add_item(StaffApplyButton("ladder", label="Nous rejoindre"))
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay(
            "## 🗳️ Ladder\n"
            "Ladder saisonnier 1v1 (AoE2).\n"
            "- `/ladder register` — s'inscrire\n"
            "- `/ladder join` — rejoindre la file\n"
            "- `/ladder leaderboard` — le classement"
        ),
        row,
    ))
    await interaction.response.send_message(view=view, ephemeral=True)
