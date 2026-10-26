"""Kingdoms Marché TCG card resolver (cartes-tcg-v2, Drasah 26/10).

The market cards live in `assets/marche/` under their DISPLAY names
(the raw upload names — renaming them on GitHub ✏️ needs no code
change as long as these mappings follow). The resolver maps each
market item KEY to its uploaded filename so the panel code only ever
speaks in config keys. A missing PNG stays cosmetic: the ephemeral
article sheet works with or without the art. The card back
(`Dos_de_carte.png`) belongs to the FUTURE booster flow and is
deliberately NOT part of this mapping (Drasah 26/10).
"""

from __future__ import annotations

from pathlib import Path

_ASSET_DIR = Path(__file__).resolve().parent / "assets" / "marche"

# item key → uploaded card filename (Drasah display names, 26/10).
_CARDS: dict[str, str] = {
    "embuscade": "Embuscade.png",
    "traquenard": "Traquenard.png",
    "contre_espionnage": "Contre_Espionnage.png",
    "sabotage": "Sabotage.png",
    "jeu_d_armes": "Jeu_darme.png",
    "explorateur": "Explorateur.png",
    "corruption": "Corruption.png",
    "garde_royale": "Garde_Royale.png",
    "patrouille": "Patrouille.png",
    "mariage_arrange": "Mariage_Arrange.png",
}


def resolve_card(item: str) -> Path | None:
    """Return the TCG card path of one market item (None = missing)."""
    name = _CARDS.get(item)
    if not name:
        return None
    candidate = _ASSET_DIR / name
    return candidate if candidate.is_file() else None


def card_filename(item: str) -> str:
    """The filename the card travels under on the wire."""
    return _CARDS.get(item, f"{item}.png")
