"""The entity card's 4000-char component text budget (Discord 50035).

A map with a long Liquipedia description (Arena, 2026-11-09) crossed
Discord's shared TextDisplay budget: the forum sync AND the admin's
per-map preview both failed with 50035. The card now clamps its
description at build time — no consumer can send an oversized card.
"""

from __future__ import annotations

from typing import Any

from botforge.discord.ui.entity_cards import map_card


def _displayable_text(node: Any) -> int:
    """Total TextDisplay text under a layout node (Discord's budget)."""
    total = len(getattr(node, "content", "") or "")
    for child in getattr(node, "children", ()) or ():
        total += _displayable_text(child)
    return total


def _button() -> Any:
    import discord

    return discord.ui.Button(label="Voir", custom_id="map:open:x:y")


def test_short_description_rides_unclamped() -> None:
    view = map_card(
        map_id="map:aoe2:arena",
        name="Arena",
        props=("land", "stone-walls"),
        description="Une carte fermée.",
        image_url="",
        icon_url="",
        source_url="https://liquipedia.net/aoe2/Arena",
        pool_links=("Pool 1",),
        buttons=(_button,),
    )
    assert _displayable_text(view) <= 4000
    assert "Une carte fermée." in _description_text(view)


def _description_text(view: Any) -> str:
    texts: list[str] = []

    def walk(node: Any) -> None:
        content = getattr(node, "content", None)
        if isinstance(content, str):
            texts.append(content)
        for child in getattr(node, "children", ()) or ():
            walk(child)

    walk(view)
    return "\n\n".join(texts)


def test_long_description_is_clamped_into_the_budget() -> None:
    paragraph = "Un paragraphe long. " * 40  # ~760 chars
    description = "\n\n".join(paragraph for _ in range(20))  # ~15k chars
    view = map_card(
        map_id="map:aoe2:arena",
        name="Arena",
        props=("land", "stone-walls"),
        description=description,
        image_url="",
        icon_url="",
        source_url="https://liquipedia.net/aoe2/Arena",
        pool_links=("Pool 1",),
        buttons=(_button,),
    )
    assert _displayable_text(view) <= 4000
    assert "truncated" in _description_text(view)
    assert "tronquée" in _description_text(
        map_card(
            map_id="map:aoe2:arena",
            name="Arena",
            props=("land", "stone-walls"),
            description=description,
            image_url="",
            icon_url="",
            source_url="https://liquipedia.net/aoe2/Arena",
            pool_links=("Pool 1",),
            buttons=(_button,),
            locale="fr",
        )
    )


def test_clamp_keeps_paragraph_boundaries() -> None:
    description = "\n\n".join(f"Paragraphe {i}." for i in range(400))
    view = map_card(
        map_id="map:aoe2:arena",
        name="Arena",
        props=(),
        description=description,
        image_url="",
        icon_url="",
        source_url="",
        pool_links=(),
        buttons=(_button,),
    )
    assert _displayable_text(view) <= 4000
    text = _description_text(view)
    assert "Paragraphe 0." in text
    assert "truncated" in text
