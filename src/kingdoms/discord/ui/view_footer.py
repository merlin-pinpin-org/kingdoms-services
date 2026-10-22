"""The view-id footer: every surface's feedback anchor.

One rule (docs/DEVELOPMENT/VIEWS.md): every rendered surface carries
its view id as its last line, backticked — the anchor every human
feedback refers to. LayoutViews get a ``-#`` small-text line outside
every container (never inside an embed); embeds carry the id as the
embed footer, backticked.
"""

from __future__ import annotations

import discord

from kingdoms.core.ids import footer


def add_view_footer(view: discord.ui.LayoutView, view_id: str) -> discord.ui.LayoutView:
    """Append the id footer line to a LayoutView, outside the containers."""
    line = footer(view_id)
    if line:
        view.add_item(discord.ui.TextDisplay(line))
    return view


def embed_footer(embed: discord.Embed, view_id: str) -> discord.Embed:
    """Set the id as an embed's footer, backticked."""
    embed.set_footer(text=f"`{view_id}`")
    return embed


__all__ = ["add_view_footer", "embed_footer"]
