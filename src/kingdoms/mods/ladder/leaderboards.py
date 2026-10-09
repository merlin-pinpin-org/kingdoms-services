"""Leaderboard selection: which provider boards a guild displays (#147).

The provider serves every board it knows (ext-librematch: rm_1v1,
rm_team, unranked, dm_1v1, dm_team); a guild picks 1 to 4 of them.
The choice lives in the guild settings (``leaderboards``), defaulting
to RM 1v1 + TG RM.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import discord

logger = logging.getLogger("kingdoms.leaderboards")

AVAILABLE_BOARDS: tuple[tuple[str, str], ...] = (
    ("rm_1v1", "RM 1v1"),
    ("rm_team", "TG RM"),
    ("unranked", "Unranked"),
    ("dm_1v1", "DM 1v1"),
    ("dm_team", "TG DM"),
)

DEFAULT_BOARDS: tuple[str, ...] = ("rm_1v1", "rm_team")
MAX_BOARDS = 4
PIN_LEADERBOARDS_SELECT_ID = "admin:pin:select:leaderboards"


async def _chosen_values(interaction: discord.Interaction) -> list[str]:
    """Read the chosen values of a select interaction."""
    data = getattr(interaction, "data", None) or {}
    return [str(v) for v in data.get("values", [])]


async def get_guild_boards(guild_id: str, db: Any) -> list[str]:
    """Read the guild's chosen boards (ordered, validated, 1-4)."""
    boards: list[str] = list(DEFAULT_BOARDS)
    try:
        settings = await db.get_guild_settings(guild_id) if db else None
        raw = (settings or {}).get("leaderboards")
        if isinstance(raw, (list, tuple)) and raw:
            valid = [b for b in raw if any(k == b for k, _ in AVAILABLE_BOARDS)]
            if valid:
                boards = valid[:MAX_BOARDS]
    except Exception:
        logger.warning("leaderboards setting read failed — defaulting", exc_info=True)
    return list(boards)


async def set_guild_boards(guild_id: str, boards: list[str], db: Any) -> list[str]:
    """Persist the guild's boards; returns the stored (validated) list."""
    valid = [b for b in boards if any(k == b for k, _ in AVAILABLE_BOARDS)]
    valid = valid[:MAX_BOARDS]
    if not valid:
        raise ValueError("at least one valid leaderboard is required")
    settings = dict(await db.get_guild_settings(guild_id) or {})
    settings["leaderboards"] = valid
    await db.set_guild_settings(guild_id, settings)
    return valid


def board_label(board_key: str) -> str:
    """Human label for a board key."""
    for key, label in AVAILABLE_BOARDS:
        if key == board_key:
            return label
    return board_key


class PinLeaderboardsSelect(
    discord.ui.DynamicItem[discord.ui.Select[Any]],
    template=r"admin:pin:select:leaderboards",
):
    """The guild's displayed leaderboards (1-4 of the provider's boards)."""

    def __init__(self, options: list[discord.SelectOption], defaults: list[str], placeholder: str = "") -> None:
        select: discord.ui.Select[Any] = discord.ui.Select(
            custom_id=PIN_LEADERBOARDS_SELECT_ID,
            options=options,
            placeholder=placeholder or None,
            min_values=1,
            max_values=4,
        )
        select._selected_values = [o for o in options if o.value in defaults]  # type: ignore[attr-defined]
        super().__init__(select)

    @classmethod
    def create(cls, defaults: list[str], placeholder: str = "") -> PinLeaderboardsSelect:
        """Build the select with every available board."""
        from kingdoms.mods.ladder.leaderboards import AVAILABLE_BOARDS

        options = [
            discord.SelectOption(label=label, value=key, description="Afficher ce leaderboard")
            for key, label in AVAILABLE_BOARDS
        ]
        return cls(options, defaults, placeholder)

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> PinLeaderboardsSelect:
        """Rebuild the select from the wire."""
        del interaction, item, match
        return cls.create([])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Apply the boards choice through the persistent handler."""
        await handle_leaderboards(interaction)


async def handle_leaderboards(interaction: discord.Interaction) -> None:
    """Persist the guild's displayed leaderboards (1-4 boards)."""
    wiring = getattr(interaction.client, "logs_service", None)
    guild_id = str(interaction.guild_id) if interaction.guild_id else ""
    if not guild_id:
        await interaction.response.send_message("Panel indisponible.", ephemeral=True)
        return
    values = await _chosen_values(interaction)
    if not values:
        await interaction.response.defer()
        return
    await interaction.response.defer()
    if wiring is None:
        return
    from kingdoms.discord.pinned_views import refresh_registered_pins
    from kingdoms.mods.ladder.leaderboards import set_guild_boards

    logs = wiring.logs_service
    db = getattr(logs, "_db", None) if logs is not None else None
    try:
        await set_guild_boards(guild_id, list(values), db)
    except ValueError:
        return
    await refresh_registered_pins(guild_id)
