"""Embed builders: sober, lightweight rich messages (ADR-0009).

Embeds are the first choice for read-mostly output; rich structured
UI belongs to Components V2 (``factory.py`` / ``screens.py``). A
message is either embed-based or V2, never both — these builders
only produce :class:`discord.Embed`, never a view.
"""

from __future__ import annotations

from typing import Any, ClassVar

import discord

__all__ = [
    "EmbedBuilder",
    "LadderEmbedBuilder",
    "UserEmbedBuilder",
]

BLURPLE = 0x5865F2
GREEN = 0x57F287
RED = 0xED4245
GOLD = 0xFEE75C

SUCCESS = GREEN
ERROR = RED
INFO = BLURPLE
WARNING = GOLD


class EmbedBuilder:
    """Builder for consistent, styled status embeds."""

    LEVELS: ClassVar[dict[str, int]] = {"success": SUCCESS, "error": ERROR, "info": INFO, "warning": WARNING}

    @staticmethod
    def build(
        title: str,
        description: str,
        level: str = "info",
        fields: list[dict[str, Any]] | None = None,
    ) -> discord.Embed:
        """Create a levelled status embed (success/error/info/warning)."""
        color = EmbedBuilder.LEVELS.get(level, INFO)
        embed = discord.Embed(title=title, description=description, color=color)
        for field in fields or []:
            embed.add_field(
                name=str(field.get("name", "")),
                value=str(field.get("value", "")),
                inline=bool(field.get("inline", True)),
            )
        return embed

    @staticmethod
    def success(title: str, description: str, fields: list[dict[str, Any]] | None = None) -> discord.Embed:
        """Create a success (green) embed."""
        return EmbedBuilder.build(title, description, "success", fields)

    @staticmethod
    def error(title: str, description: str, fields: list[dict[str, Any]] | None = None) -> discord.Embed:
        """Create an error (red) embed."""
        return EmbedBuilder.build(title, description, "error", fields)

    @staticmethod
    def info(title: str, description: str, fields: list[dict[str, Any]] | None = None) -> discord.Embed:
        """Create an info (blue) embed."""
        return EmbedBuilder.build(title, description, "info", fields)

    @staticmethod
    def warning(title: str, description: str, fields: list[dict[str, Any]] | None = None) -> discord.Embed:
        """Create a warning (gold) embed."""
        return EmbedBuilder.build(title, description, "warning", fields)


class UserEmbedBuilder:
    """Builder for user-facing embeds (profile, registration confirmation)."""

    @staticmethod
    def user_profile(user: dict[str, Any]) -> discord.Embed:
        """Create a user profile embed."""
        embed = discord.Embed(title=f"{user.get('name', 'Unknown')} — Profile", color=BLURPLE)
        embed.add_field(name="Game", value=str(user.get("game", "N/A")), inline=True)
        embed.add_field(name="ELO", value=str(user.get("elo", 0)), inline=True)
        embed.add_field(name="Wins", value=str(user.get("wins", 0)), inline=True)
        embed.add_field(name="Losses", value=str(user.get("losses", 0)), inline=True)
        embed.add_field(name="Registered", value=str(user.get("registered_at", "N/A")), inline=False)
        return embed

    @staticmethod
    def registration_confirmation(user: dict[str, Any]) -> discord.Embed:
        """Create a registration confirmation embed."""
        embed = discord.Embed(
            title="Registration Complete!",
            description=f"Welcome, {user.get('name', 'User')}!",
            color=GREEN,
        )
        embed.add_field(name="Game", value=str(user.get("game", "N/A")), inline=True)
        embed.add_field(name="Status", value="Active", inline=True)
        return embed


class LadderEmbedBuilder:
    """Builder for ladder/match embeds."""

    @staticmethod
    def match_notification(opponent: str, match_id: str) -> discord.Embed:
        """Create a match notification embed."""
        embed = discord.Embed(
            title="Match Ready!",
            description=f"You have been matched against **{opponent}**",
            color=GOLD,
        )
        embed.add_field(name="Match ID", value=match_id, inline=True)
        embed.add_field(name="Status", value="Waiting for confirmation", inline=True)
        return embed

    @staticmethod
    def ladder_rankings(rankings: list[dict[str, Any]]) -> discord.Embed:
        """Create a ladder rankings embed (top 10)."""
        embed = discord.Embed(title="Ladder Rankings", color=BLURPLE)
        for i, ranking in enumerate(rankings[:10], 1):
            user = ranking.get("user", {})
            embed.add_field(
                name=f"#{i} {user.get('name', 'Unknown')}",
                value=f"ELO: {ranking.get('elo', 0)} | Wins: {ranking.get('wins', 0)}",
                inline=False,
            )
        return embed
