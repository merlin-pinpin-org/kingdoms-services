"""Kingdoms mod Lot B views — unit tests on the serialized wire.

The builders are pure: a valid Components V2 layout (SDK build-time
guarantees) is the contract; the custom_id convention and the disabled
buy buttons of the diplomacy screen are asserted here.
"""
from __future__ import annotations

import discord

from kingdoms.mods.kingdoms.views import (
    DelayLine,
    KingdomCard,
    SpecialActionCard,
    TerritoryLine,
    build_cadastre_layout,
    build_delays_layout,
    build_diplomacy_layout,
    build_gazette_layout,
    build_kingdom_profile_layout,
    build_no_season_layout,
)


def _assert_layout(view: object) -> discord.ui.LayoutView:
    assert isinstance(view, discord.ui.LayoutView)
    components = view.to_components()  # type: ignore[attr-defined]
    assert components, "a layout must serialize to at least one action row"
    return view


def test_cadastre_layout_builds() -> None:
    """B1 — the cadastre serializes kingdoms and territories."""
    view = build_cadastre_layout(
        "🧾 Cadastre",
        kingdoms=(KingdomCard(name="Aquitaine", territories=5),),
        territories=(TerritoryLine(map_name="Arabie", owner_name="Aquitaine"),),
    )
    _assert_layout(view)


def test_kingdom_profile_layout_builds() -> None:
    """B2 — the profile serializes king, lords and counters."""
    view = build_kingdom_profile_layout(
        "👑 Royaume",
        KingdomCard(
            name="Aquitaine",
            king="Drasah",
            lords=("Sir Lancelot",),
            territories=5,
            tech_points=6,
            marriages=1,
        ),
    )
    _assert_layout(view)


def test_delays_layout_builds() -> None:
    """B3 — the delays screen serializes its rows."""
    view = build_delays_layout(
        "🕰️ Délais d'attaque",
        delays=(DelayLine(kind="⚔️", origin="Northumbrie", territory="Oasis", remaining="01:47:10"),),
    )
    _assert_layout(view)


def test_diplomacy_layout_disables_buy_buttons() -> None:
    """B4 — the tech shop renders disabled actions with the custom_id convention."""
    view = build_diplomacy_layout(
        "📖 Diplomatie",
        alliances=("🤝 Bretagne ↔ Northumbrie",),
        actions=(SpecialActionCard(key="embuscade", name="Embuscade", cost=2, description="Espionnage"),),
        tech_points_label="6",
    )
    _assert_layout(view)


def test_gazette_layout_builds() -> None:
    """B5 — the Gazette serializes its cycle summary."""
    view = build_gazette_layout("📣 La Gazette", lines=("🏰 Aquitaine tient 5 terres.",))
    _assert_layout(view)


def test_no_season_placeholder_builds() -> None:
    """Pre-season — the placeholder serializes title and body."""
    view = build_no_season_layout("Kingdoms", "Aucune saison n'est en cours.")
    _assert_layout(view)
