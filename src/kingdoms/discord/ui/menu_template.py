"""The menu template — one charte for every pinned menu of the tree.

Every home and admin menu follows the same design (the sessions'
blueprint): a title, a one-paragraph description, sections of
buttons (each section: heading + one row of up to 5 buttons), and
the view id as the footer — clean, contextualised, translated.
Buttons open ephemeral views (the frozen-menu contract); the
builders stay game/mod-agnostic so any new perimeter reuses the
charte as-is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import discord

ACCENT = 0x5865F2


@dataclass(frozen=True)
class MenuSection:
    """One section of a menu: heading key + its buttons."""

    heading_key: str = ""
    buttons: tuple[tuple[str, str, str], ...] = ()  # (custom_id, label, emoji)


@dataclass(frozen=True)
class MenuSpec:
    """One pinned menu's content, charte-compliant."""

    title: str
    description: str = ""
    view_id: str = ""
    sections: tuple[MenuSection, ...] = ()
    extra_lines: tuple[str, ...] = field(default_factory=tuple)


def build_menu(spec: MenuSpec) -> discord.ui.LayoutView:
    """Build one menu on the charte: title, description, sections, id.

    Layout: one accent container holding the header (title +
    description), each section (heading + a row of buttons), the
    extra text lines, and the view id as the small-text footer.
    """
    blocks: list[Any] = [discord.ui.TextDisplay(f"# {spec.title}")]
    if spec.description:
        blocks.append(discord.ui.TextDisplay(spec.description))
    for section in spec.sections:
        if not section.buttons:
            continue
        row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
        for custom_id, label, emoji in section.buttons[:5]:
            row.add_item(
                discord.ui.Button(
                    custom_id=custom_id,
                    label=label,
                    emoji=discord.PartialEmoji.from_str(emoji) if emoji else None,
                    style=discord.ButtonStyle.secondary,
                )
            )
        if section.heading_key:
            blocks.append(discord.ui.Separator())
            blocks.append(discord.ui.TextDisplay(f"## {section.heading_key}"))
        blocks.append(row)
    for line in spec.extra_lines:
        blocks.append(discord.ui.TextDisplay(line))
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(*blocks, accent_colour=discord.Colour(ACCENT)))
    if spec.view_id:
        view.add_item(discord.ui.TextDisplay(f"-# `{spec.view_id}`"))
    return view


__all__ = ["ACCENT", "MenuSection", "MenuSpec", "build_menu"]
