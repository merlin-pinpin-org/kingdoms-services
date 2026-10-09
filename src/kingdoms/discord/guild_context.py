"""Guild-context resolution for cross-guild admin surfaces (reusable).

Home views and admin panels are guild-scoped: in a guild the scope is
the guild itself, in a bot admin's DM the user must pick which guild
the view targets. This module owns that flow once — every guild-scoped
entry point calls ``require_guild_context`` and receives either the
guild id, or None (the guild picker was answered, the caller returns).
"""

from __future__ import annotations

import logging
import re
from typing import Any

import discord

logger = logging.getLogger("kingdoms.guild_context")

_PICK_NS = "guild:context:pick"


def _guild_options(client: discord.Client) -> list[discord.SelectOption]:
    """List the bot's shared guilds as picker options (25 max)."""
    return [
        discord.SelectOption(label=guild.name, value=str(guild.id))
        for guild in list(client.guilds)[:25]
    ]


class GuildContextPicker(
    discord.ui.DynamicItem[discord.ui.Select[Any]],
    template=rf"{_PICK_NS}:(?P<view_key>[A-Za-z0-9:_-]+)",
):
    """The DM-side guild picker: one option per shared guild, then re-dispatch.

    The target entry point rides the custom_id so the pick re-opens the
    exact requested view for the chosen guild.
    """

    def __init__(self, view_key: str, options: list[discord.SelectOption]) -> None:
        self.view_key = view_key
        super().__init__(
            discord.ui.Select(
                custom_id=f"{_PICK_NS}:{view_key}"[:100],
                options=options or [discord.SelectOption(label="Aucune guilde", value="none")],
                placeholder="Pour quelle guilde ?",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> GuildContextPicker:
        """Rebuild the picker at click time (fresh guild options)."""
        del item
        return cls(match.group("view_key"), _guild_options(interaction.client))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Re-dispatch the requested entry point for the chosen guild."""
        chosen = (self.item.values or [""])[0]
        if not chosen or chosen == "none":
            await interaction.response.defer()
            return
        from kingdoms.discord.home import open_home_view_for_guild

        await open_home_view_for_guild(interaction, self.view_key, chosen)


def _picker_view(view_key: str, options: list[discord.SelectOption]) -> discord.ui.LayoutView:
    """Build the ephemeral picker layout for one entry point."""
    view = discord.ui.LayoutView(timeout=None)
    row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    row.add_item(GuildContextPicker(view_key, options))
    view.add_item(row)
    return view


async def require_guild_context(
    interaction: discord.Interaction,
    view_key: str,
) -> str | None:
    """Resolve the guild scope for a guild-scoped view; None when answered.

    In a guild the interaction's guild is the scope. In a DM the user
    gets the ephemeral guild picker and the caller returns — the pick
    re-dispatches the same view key with the chosen guild.
    """
    if interaction.guild_id is not None:
        return str(interaction.guild_id)
    view = _picker_view(view_key, _guild_options(interaction.client))
    text = "Cette vue est li\u00e9e \u00e0 une guilde \u2014 de laquelle s'agit-il ?"
    prompt: discord.ui.TextDisplay[Any] = discord.ui.TextDisplay(text)
    view.add_item(discord.ui.Container(prompt))
    await interaction.response.send_message(view=view, ephemeral=True)
    return None


def register_guild_context_items(bot: discord.Client) -> None:
    """Register the picker's DynamicItem (called at every startup)."""
    bot.add_dynamic_items(GuildContextPicker)
