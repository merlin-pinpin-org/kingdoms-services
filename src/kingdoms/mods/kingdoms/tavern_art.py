"""Kingdoms Taverne artwork resolver (taverne-immersion-v1, Drasah 26/10).

The three Taverne visuals live in ``assets/taverne/``. The README
contract asks for canonical names (``taverne-entree.png``,
``taverne-comptoir.png``, ``taverne-mercenaire.png``) but the files
were uploaded with their raw tool names — the GitHub contents API
cannot rename a binary from this side, so the resolver tries the
CANONICAL name first and falls back to the uploaded alias. Renaming
the files by hand (GitHub ✏️) needs no code change: the canonical
name wins as soon as it exists.
"""

from __future__ import annotations

from pathlib import Path

_ASSET_DIR = Path(__file__).resolve().parent / "assets" / "taverne"

_CANONICAL: dict[str, str] = {
    "entree": "taverne-entree.png",
    "comptoir": "taverne-comptoir.png",
    "mercenaire": "taverne-mercenaire.png",
}

# The uploaded aliases (raw image-generation file names of 26/10).
_FALLBACKS: dict[str, tuple[str, ...]] = {
    "entree": ("image.png",),
    "comptoir": ("9c1b722d-2dba-4783-99dc-ee977bbf2357.jpg",),
    "mercenaire": ("fd9e7f9c-5c61-46c9-aae2-67e0ba606757.jpg",),
}


def resolve_art(which: str) -> Path | None:
    """Return the artwork path (canonical first, alias fallback)."""
    for name in (_CANONICAL.get(which, ""), *(_FALLBACKS.get(which, ()))):
        if not name:
            continue
        candidate = _ASSET_DIR / name
        if candidate.is_file():
            return candidate
    return None


def art_filename(which: str) -> str:
    """The canonical filename to give the art on the wire (ext kept)."""
    path = resolve_art(which)
    if path is None:
        return _CANONICAL.get(which, f"taverne-{which}.png")
    return _CANONICAL[which] if path.name == _CANONICAL.get(which) else path.name
