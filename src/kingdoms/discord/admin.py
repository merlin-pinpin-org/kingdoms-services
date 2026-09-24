"""The /admin command: operator panel built as a Components V2 layout.

First command using a ``LayoutView`` (ADR-0009 dual Discord UI system):
instead of a plain embed, the answer is a Components V2 layout — a
container with a text header and a section whose button reacts live.
It demonstrates the layout pattern further admin features will reuse
(section + accessory button, ``<mod>:<component>:<payload>`` ids).

Reference: kingdoms-services#102.
"""

from __future__ import annotations

import discord
from discord import app_commands

PING_BUTTON_ID = "admin:button:ping"


class AdminLayout(discord.ui.LayoutView):
    """The /admin answer: a Components V2 layout with a ping button."""

    def __init__(self) -> None:
        super().__init__(timeout=300)
        button: discord.ui.Button[AdminLayout] = discord.ui.Button(
            label="Ping",
            style=discord.ButtonStyle.primary,
            custom_id=PING_BUTTON_ID,
        )
        button.callback = self.on_ping  # type: ignore[method-assign]
        container = discord.ui.Container(
            discord.ui.TextDisplay("# Kingdoms — Admin"),
            discord.ui.Section(
                discord.ui.TextDisplay("Operator panel. Ping checks that the bot reacts to clicks."),
                accessory=button,
            ),
        )
        self.add_item(container)

    async def on_ping(self, interaction: discord.Interaction) -> None:
        """Answer the ping button click with a visible pong."""
        await interaction.response.send_message("pong", ephemeral=True)


def build_admin_layout() -> AdminLayout:
    """Build the /admin layout (standalone for tests)."""
    return AdminLayout()


def register_admin_command(
    tree: app_commands.CommandTree[discord.Client],
) -> None:
    """Register the /admin slash command on the command tree."""

    @tree.command(name="admin", description="Admin panel (operators)")
    async def admin_command(interaction: discord.Interaction) -> None:
        """Answer the /admin interaction with the layout view."""
        await interaction.response.send_message(view=build_admin_layout(), ephemeral=True)
