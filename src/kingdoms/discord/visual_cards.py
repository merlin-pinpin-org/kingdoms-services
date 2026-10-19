"""Visual forum cards: one generated image instead of heavy embeds/views.

Some forum posts are essentially *one picture plus a lot of facts*: a
faction's sheet, a map's presentation, a player's profile. Rendering
them as a single OCR-verifiable image (built by the visual-generator
library from JSON data) replaces multi-block LayoutViews and long
embeds with a lighter, brand-stable post — and the same JSON can be
re-rendered for any destination (forum post, announcement message).

Decision guide (when to use what):

- **Visual card** — read-only content, one entity, structured facts
  and/or a couple of images: faction posts, map posts, player
  profiles. Generate, attach to the post, keep the textual content as
  the searchable fallback (the library OCR-guarantees the image says
  the same thing).
- **Embed (ADR-0009)** — short status/confirmation/ladder output.
- **Components V2 (LayoutView)** — anything interactive, or content
  that must re-render per audience without a new image.

The library is the only renderer: this module maps platform entities
onto the library's JSON visual schema (``info_card`` for a picture
plus facts, ``media_card`` for image strips plus a synthesized
description) and renders through the installed ``visual-generator``
package (see pyproject dependency group ``visuals`` and the library's
agent skill for schemas, CLIs and the OCR validation contract).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("kingdoms.discord.visual_cards")

ASSETS_DIR = os.path.join("data", "visual_cards", "assets")
CARDS_DIR = os.path.join("data", "visual_cards", "rendered")


@dataclass(frozen=True, slots=True)
class RenderedCard:
    """One rendered visual card: file path + caption text."""

    path: str
    caption: str


def _ensure_dirs() -> None:
    os.makedirs(ASSETS_DIR, exist_ok=True)
    os.makedirs(CARDS_DIR, exist_ok=True)


def _render(visual: dict[str, Any]) -> str:
    """Render one library visual to a file, return the absolute path.

    Fail-closed: an unreadable asset or unknown template aborts the
    caller's card generation (the post falls back to its LayoutView).
    """
    from engine.render import render_visual

    path: str = render_visual(visual)
    return os.path.abspath(path)


def faction_card(
    faction: Any,
    content: tuple[str, str, str, str],
    tags: list[str],
    facts: list[tuple[str, list[list[str]]]],
) -> RenderedCard:
    """Build one faction's info card (one image + structured facts).

    ``content`` is the ``(name, summary, source_url, image_url)``
    tuple from :func:`kingdoms.discord.content_posts.entity_post_content`.
    ``facts`` are label/value pairs extracted from the summary by the
    caller (e.g. strengths, unique unit).
    """
    _ensure_dirs()
    name, summary, source, image = content
    slug = str(getattr(faction, "id", name)).replace(":", "_").replace("/", "_")
    sections: dict[str, Any] = {
            "title": name,
            "subtitle": f"Civilisation — {getattr(faction, 'game_key', '')}".rstrip(" —"),
            "tags": tags[:6],
            "columns": [{"head": head, "lines": lines} for head, lines in facts][:2],
            "footer": (f"Source : {source}" if source else "") + f" · {slug}",
    }
    visual: dict[str, Any] = {
        "template": "info_card",
        "output": os.path.join(CARDS_DIR, f"faction_{slug}.jpg"),
        "sections": sections,
    }
    if image:
        sections["image"] = image
    if summary and not facts:
        # the summary carries the facts; when no facts table is given,
        # keep the text readable on the card as a single column
        sections["columns"] = [{"head": "Aperçu", "lines": [["Résumé", summary[:180]]]}]
    path = _render(visual)
    return RenderedCard(path=path, caption=f"**{name}** — fiche civilisation")


def map_card(
    map_entry: Any,
    images: list[str],
    tags: list[str],
    facts: list[list[str]],
    description: str,
) -> RenderedCard:
    """Build one map's media card (image strip + synthesized description).

    The description must be *synthesized*: the card wraps and visibly
    clips it — never dump the raw map description, condense it first
    (the game designer / agent decides what lands on the card).
    """
    _ensure_dirs()
    slug = str(getattr(map_entry, "id", getattr(map_entry, "name", "map"))).replace(":", "_").replace("/", "_")
    sections: dict[str, Any] = {
        "title": str(getattr(map_entry, "name", slug)),
        "subtitle": "Map",
        "tags": tags[:6],
        "images": images[:4],
        "facts": facts[:5],
        "description": description,
        "footer": str(getattr(map_entry, "id", slug)),
    }
    visual: dict[str, Any] = {
        "template": "media_card",
        "output": os.path.join(CARDS_DIR, f"map_{slug}.jpg"),
        "sections": sections,
    }
    path = _render(visual)
    return RenderedCard(path=path, caption=f"**{getattr(map_entry, 'name', slug)}** — fiche map")


def player_card(
    display_name: str,
    avatar_url: str | None,
    games: list[list[str]],
    profile_ids: list[list[str]],
) -> RenderedCard:
    """Build one player's info card (avatar + games + profile ids)."""
    _ensure_dirs()
    slug = display_name.replace(" ", "_").replace("/", "_")
    sections: dict[str, Any] = {
        "title": display_name,
        "subtitle": "Joueur",
        "tags": [g[0] for g in games[:6]],
        "columns": [
            {"head": "Jeux", "lines": games[:2]},
            {"head": "Profils", "lines": profile_ids[:2]},
        ],
        "footer": f"user:{slug}",
    }
    if avatar_url:
        sections["image"] = avatar_url
        sections["image_caption"] = "Avatar"
    visual: dict[str, Any] = {
        "template": "info_card",
        "output": os.path.join(CARDS_DIR, f"player_{slug}.jpg"),
        "sections": sections,
    }
    path = _render(visual)
    return RenderedCard(path=path, caption=f"**{display_name}** — profil joueur")


def attach(card: RenderedCard) -> dict[str, Any]:
    """Discord.py attachment kwargs for one rendered card.

    Usage with the platform senders::

        await platform.send_layout(
            guild_id, channel_id, layout,
            attachments=[discord.File(card.path, filename=os.path.basename(card.path))],
        )
    """
    import discord

    return {"file": discord.File(card.path, filename=os.path.basename(card.path))}


__all__ = [
    "RenderedCard",
    "attach",
    "faction_card",
    "map_card",
    "player_card",
]
