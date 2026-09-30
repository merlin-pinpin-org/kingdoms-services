"""Kingdoms mod — Lot B screens: the game designer's readable views.

Pure builders over the UI SDK bricks (ADR-0009): every function takes
plain, pre-localized data and returns a Components V2 LayoutView.
No service reads, no Discord state — the Discord surface (Lot C wiring)
feeds them; unit tests assert on the serialized wire only.

Reference: docs/MODS/kingdoms/ (kingdoms repo), Lot B of the
implementation plan — cadastre (B1), kingdom profile (B2), attack
delays (B3), diplomacy (B4), gazette (B5).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import discord

from kingdoms.discord.ui.factory import (
    BLURPLE,
    Action,
    Container,
    Row,
    Separator,
    Text,
    UILayout,
)

MOD_KEY = "kingdoms"


async def _noop(interaction: discord.Interaction) -> None:
    """Placeholder callback: disabled buttons never wire real actions."""
    del interaction


@dataclass(frozen=True, slots=True)
class KingdomCard:
    """One kingdom line of the cadastre/profile screens (B1/B2)."""

    name: str
    emblem: str = "🏰"
    king: str = ""
    lords: tuple[str, ...] = ()
    territories: int = 0
    tech_points: int = 0
    marriages: int = 0
    is_gaia: bool = False


@dataclass(frozen=True, slots=True)
class TerritoryLine:
    """One territory line of the cadastre screen (B1)."""

    map_name: str
    owner_name: str
    emblem: str = "🗺️"


@dataclass(frozen=True, slots=True)
class DelayLine:
    """One programmed attack/defense row of the delays screen (B3)."""

    kind: str
    origin: str
    territory: str
    remaining: str


@dataclass(frozen=True, slots=True)
class SpecialActionCard:
    """One purchasable special action of the diplomacy screen (B4).

    ``key`` is the slug used in custom_ids and config lookups; the
    name is the pre-localized display label.
    """

    key: str
    name: str
    cost: int
    description: str = ""


def build_cadastre_layout(
    title: str,
    kingdoms: Sequence[KingdomCard],
    territories: Sequence[TerritoryLine],
    *,
    season_label: str = "",
    footer: str = "",
) -> discord.ui.LayoutView:
    """B1 — the cadastre: territory ownership per kingdom, map by map."""
    container = Container(accent=BLURPLE).add(Text(f"# {title}"))
    if season_label:
        container = container.add(Text(f"-# {season_label}"))
    container = container.add(Separator())
    for kingdom in kingdoms:
        container = container.add(Text(f"**{kingdom.emblem} {kingdom.name}** — {kingdom.territories}"))
    container = container.add(Separator())
    for territory in territories:
        container = container.add(Text(f"{territory.emblem} {territory.map_name} — {territory.owner_name}"))
    if footer:
        container = container.add(Separator()).add(Text(f"-# {footer}"))
    return UILayout().add(container).build()


def build_kingdom_profile_layout(
    title: str,
    kingdom: KingdomCard,
    *,
    footer: str = "",
) -> discord.ui.LayoutView:
    """B2 — one kingdom's profile: king, lords, tech, marriages."""
    container = Container(accent=BLURPLE).add(Text(f"# {kingdom.emblem} {title}"))
    container = container.add(Text(f"**{kingdom.name}**"))
    if kingdom.king:
        container = container.add(Text(f"👑 {kingdom.king}"))
    for lord in kingdom.lords:
        container = container.add(Text(f"🎖️ {lord}"))
    container = container.add(Separator())
    container = container.add(Text(f"🗺️ {kingdom.territories}"))
    container = container.add(Text(f"🔬 {kingdom.tech_points}"))
    container = container.add(Text(f"💍 {kingdom.marriages}"))
    if footer:
        container = container.add(Separator()).add(Text(f"-# {footer}"))
    return UILayout().add(container).build()


def build_delays_layout(
    title: str,
    delays: Sequence[DelayLine],
    *,
    footer: str = "",
) -> discord.ui.LayoutView:
    """B3 — programmed attacks and their countdowns."""
    container = Container(accent=BLURPLE).add(Text(f"# {title}"))
    if not delays:
        container = container.add(Text("—"))
    for delay in delays:
        container = container.add(
            Text(f"{delay.kind} {delay.origin} — {delay.territory} — {delay.remaining}")
        )
    if footer:
        container = container.add(Separator()).add(Text(f"-# {footer}"))
    return UILayout().add(container).build()


def build_diplomacy_layout(
    title: str,
    alliances: Sequence[str],
    actions: Sequence[SpecialActionCard],
    *,
    tech_points_label: str,
    footer: str = "",
) -> discord.ui.LayoutView:
    """B4 — diplomacy: alliances plus the technology shop (C4 preview).

    The buy buttons are rendered disabled in their own action row: the
    purchase workflow is a Lot C wiring, not a Lot B view — a disabled
    button never wires its callback (SDK rule).
    """
    container = Container(accent=BLURPLE).add(Text(f"# {title}"))
    container = container.add(Text(f"🔬 {tech_points_label}"))
    container = container.add(Separator())
    for alliance in alliances:
        container = container.add(Text(alliance))
    for action in actions:
        container = container.add(Text(f"**{action.name}** — {action.cost} 🔬"))
        if action.description:
            container = container.add(Text(action.description))
        button = Action(
            action.name,
            f"{MOD_KEY}:tech:{action.key}",
            _noop,
            disabled=True,
        )
        container = container.add(Row(button))
    if footer:
        container = container.add(Separator()).add(Text(f"-# {footer}"))
    return UILayout().add(container).build()


def build_gazette_layout(
    title: str,
    lines: Sequence[str],
    *,
    footer: str = "",
) -> discord.ui.LayoutView:
    """B5 — the Gazette: the weekly cycle summary (published at the cycle switch, D1)."""
    container = Container(accent=BLURPLE).add(Text(f"# {title}"))
    for line in lines:
        container = container.add(Text(line))
    if footer:
        container = container.add(Separator()).add(Text(f"-# {footer}"))
    return UILayout().add(container).build()


def build_no_season_layout(title: str, body: str, *, footer: str = "") -> discord.ui.LayoutView:
    """The placeholder shown before the admin launches a season (D38/D52)."""
    container = Container(accent=BLURPLE).add(Text(f"# {title}"))
    container = container.add(Text(body))
    if footer:
        container = container.add(Separator()).add(Text(f"-# {footer}"))
    return UILayout().add(container).build()
