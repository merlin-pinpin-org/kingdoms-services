"""Components V2 layouts: rich structured cards (ADR-0009).

Components V2 complements embeds — rich, structured, interactive UI
for the cases a flat embed cannot carry. A message is either
embed-based or V2, never both. Built on the declarative SDK
(:mod:`kingdoms.discord.ui.factory`) so the Discord constraints
(4000-char shared text budget, 40 components, Section accessory
rules) are enforced at build time, never at Discord's door.

The layouts here are the reusable archetypes: a levelled status card,
a user profile card with thumbnail accessory, a ladder ranking card.
Features compose them — they never build ``discord.ui`` objects
directly.
"""

from __future__ import annotations

from typing import Any

import discord

from kingdoms.discord.ui.factory import (
    BLURPLE,
    GREEN,
    Container,
    Section,
    Separator,
    Text,
    Thumbnail,
    UILayout,
)

__all__ = [
    "LadderLayout",
    "StatusLayout",
    "UserProfileLayout",
    "build_ladder_layout",
    "build_status_layout",
    "build_user_profile_layout",
]

_LEVEL_COLOURS = {"success": GREEN, "error": 0xED4245, "info": BLURPLE, "warning": 0xFEE75C}


def build_status_layout(
    title: str,
    description: str,
    level: str = "info",
    fields: list[dict[str, Any]] | None = None,
) -> discord.ui.LayoutView:
    """Build a levelled status card (V2 counterpart of EmbedBuilder)."""
    accent = _LEVEL_COLOURS.get(level, BLURPLE)
    blocks: list[Any] = [Text(f"# {title}"), Text(description)]
    if fields:
        lines = "\n".join(f"**{f.get('name', '')}:** {f.get('value', '')}" for f in fields)
        blocks.append(Separator())
        blocks.append(Text(lines))
    blocks.append(Text("-# Kingdoms"))
    return UILayout().add(Container(accent=accent).add(*blocks)).build()


def build_user_profile_layout(user: dict[str, Any]) -> discord.ui.LayoutView:
    """Build a user profile card with a thumbnail accessory (V2)."""
    avatar_url = str(user.get("avatar_url", "")) or None
    section = Section(
        Text(
            f"**{user.get('name', 'Unknown')} — Profile**\n"
            f"Game: {user.get('game', 'N/A')} • ELO: {user.get('elo', 0)}\n"
            f"Wins: {user.get('wins', 0)} • Losses: {user.get('losses', 0)}\n"
            f"Registered: {user.get('registered_at', 'N/A')}"
        ),
        thumbnail=Thumbnail(avatar_url or "https://cdn.discordapp.com/embed/avatars/0.png"),
    )
    return (
        UILayout().add(Container(accent=BLURPLE).add(section).add(Separator()).add(Text("-# Kingdoms profile"))).build()
    )


def build_ladder_layout(rankings: list[dict[str, Any]]) -> discord.ui.LayoutView:
    """Build a ladder ranking card (top 10, V2 counterpart of the embed)."""
    lines = ["# Ladder Rankings"]
    for i, ranking in enumerate(rankings[:10], 1):
        user = ranking.get("user", {})
        lines.append(
            f"**#{i}** {user.get('name', 'Unknown')} — ELO: {ranking.get('elo', 0)} | Wins: {ranking.get('wins', 0)}"
        )
    return (
        UILayout()
        .add(Container(accent=BLURPLE).add(Text("\n".join(lines))).add(Separator()).add(Text("-# Kingdoms ladder")))
        .build()
    )


class StatusLayout:
    """Class-shaped adapter over :func:`build_status_layout`.

    Callers that prefer the spec's class shape build through it; the
    function form stays the primary path.
    """

    def __init__(
        self, title: str, description: str, level: str = "info", fields: list[dict[str, Any]] | None = None
    ) -> None:
        self._view = build_status_layout(title, description, level, fields)

    def build(self) -> discord.ui.LayoutView:
        """Return the assembled Components V2 view."""
        return self._view


class UserProfileLayout:
    """Class-shaped adapter over :func:`build_user_profile_layout`."""

    def __init__(self, user: dict[str, Any]) -> None:
        self._view = build_user_profile_layout(user)

    def build(self) -> discord.ui.LayoutView:
        """Return the assembled Components V2 view."""
        return self._view


class LadderLayout:
    """Class-shaped adapter over :func:`build_ladder_layout`."""

    def __init__(self, rankings: list[dict[str, Any]]) -> None:
        self._view = build_ladder_layout(rankings)

    def build(self) -> discord.ui.LayoutView:
        """Return the assembled Components V2 view."""
        return self._view
