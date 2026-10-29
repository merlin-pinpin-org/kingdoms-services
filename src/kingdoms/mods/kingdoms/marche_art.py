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


# marche-visuel-v2 (Drasah 28/10): the market banner (the generated
# crier scene, uploaded by Drasah) — cosmetic, the panel works without
# it (pattern assets/taverne).
# marche-banner-live-v1 (Drasah 29/10): the web upload landed at the
# repo ROOT as « marché.jpg » (the path field of the GitHub uploader was
# left untouched — same story as the VS badge).
# marche-banner-live-v2 (Drasah 29/10): the container resolves the
# package from site-packages (the wheel) unless PYTHONPATH puts the
# checkout first — candidates cover the canonical package path, the
# repo root (parents[4], checkout layout), AND the workdir (/app in the
# container). First existing file wins; None = no art, no crash.
_BANNER_CANDIDATES: tuple[Path, ...] = (
    _ASSET_DIR.parent / "marche_banner.png",
    Path(__file__).resolve().parents[4] / "marché.jpg",
    Path.cwd() / "marché.jpg",
)


def banner_file() -> Path | None:
    """Return the market banner path (None = asset not uploaded yet)."""
    for candidate in _BANNER_CANDIDATES:
        if candidate.is_file():
            return candidate
    return None


# chambre-des-comptes-v4 (Drasah 29/10): the held-cards MOSAIC.
#
# Drasah's live report on v3: the 728x1024 TCG cards sent as individual
# attachments render ENORMOUS in the ephemeral and drown the text.
# Decision: ONE composite image, pattern of the civ-crest mosaics —
# each card downscaled to a small tile, side by side, its X<stock>
# counter painted on the caption strip above it (« il faudrait indiqué
# aussi la quantité »). The encoded PNG is CACHED per fingerprint
# (« toujours en cache pour soulager ») — an unchanged hand replays
# the previous bytes with zero decodes and zero compositing.
from kingdoms.mods.kingdoms.civ_crests import (  # noqa: E402  (appended block)
    MOSAIC_CAPTION_H,
    MOSAIC_CAPTION_Y,
    MOSAIC_COLS,
    _decode_png_rgba,
    _draw_caption,
    _encode_png_rgba,
    _paste,
    _resample_rgba,
)

HELD_CARD_W = 128
"""Tile width of one held card (matches the civ-crest tile grid)."""

HELD_CARD_H = 180
"""Tile height: the 728x1024 cards keep their ~7:10 portrait ratio."""

HELD_TILE_H = MOSAIC_CAPTION_Y + MOSAIC_CAPTION_H + 2 + HELD_CARD_H
"""Caption strip (2px margin + 20px strip + 2px gap) + the card art."""

_HELD_CACHE: dict[str, bytes] = {}
_HELD_CACHE_MAX = 32


def held_cards_mosaic_png(held: list[tuple[str, int]]) -> bytes | None:
    """Compose the held cards into ONE mosaic PNG (None = empty hand).

    ``held`` pairs each card key with its REMAINING stock; duplicates
    collapse to their first entry. Every tile carries the card's TCG
    art downscaled to 128x180 plus its X<stock> counter painted on
    the caption strip. A missing or undecodable card keeps its tile
    (caption only) — the mosaic always goes out complete.
    """
    tiles: list[tuple[str, int]] = []
    seen: set[str] = set()
    for key, stock in held or ():
        card = str(key).strip()
        if not card or card in seen:
            continue
        seen.add(card)
        tiles.append((card, max(1, int(stock or 1))))
    if not tiles:
        return None
    fingerprint = "ledger-cards:" + ";".join(f"{k}={s}" for k, s in tiles)
    cached = _HELD_CACHE.get(fingerprint)
    if cached is not None:
        return cached
    rows = (len(tiles) + MOSAIC_COLS - 1) // MOSAIC_COLS
    canvas_w = MOSAIC_COLS * HELD_CARD_W
    canvas_h = rows * HELD_TILE_H
    canvas = bytearray(canvas_w * canvas_h * 4)
    for index, (key, stock) in enumerate(tiles):
        tile_x = (index % MOSAIC_COLS) * HELD_CARD_W
        tile_y = (index // MOSAIC_COLS) * HELD_TILE_H
        art_y = tile_y + MOSAIC_CAPTION_Y + MOSAIC_CAPTION_H + 2
        decoded = None
        path = resolve_card(key)
        if path is not None:
            try:
                decoded = _decode_png_rgba(path.read_bytes())
            except Exception:
                decoded = None
        if decoded is not None:
            art_w, art_h, art_pixels = decoded
            art = _resample_rgba(art_w, art_h, art_pixels, HELD_CARD_W, HELD_CARD_H)
            _paste(canvas, canvas_w, tile_x, art_y, HELD_CARD_W, HELD_CARD_H, art)
        _draw_caption(canvas, canvas_w, tile_x, tile_y, f"X{stock}")
    png = _encode_png_rgba(canvas_w, canvas_h, bytes(canvas))
    if len(_HELD_CACHE) >= _HELD_CACHE_MAX:
        _HELD_CACHE.pop(next(iter(_HELD_CACHE)))
    _HELD_CACHE[fingerprint] = png
    return png
