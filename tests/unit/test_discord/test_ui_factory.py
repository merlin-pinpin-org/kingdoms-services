"""Unit tests for the Discord UI SDK (kingdoms.discord.ui.factory).

The SDK hides the discord.py Components V2 machinery behind
declarative bricks (Text, Section, Row, Button, Container). These
tests pin the reliability guarantees: the layout rules Discord
enforces server-side (text and component budgets, Section accessory
constraints, Row size) are enforced at build time, with a clear
UILayoutError, before anything is sent.
"""

from __future__ import annotations

import pytest

from kingdoms.discord.ui import (
    BLURPLE,
    Button,
    Container,
    Row,
    Section,
    Separator,
    Text,
    Thumbnail,
    UIEmbed,
    UILayout,
    UILayoutError,
)

TYPE_TEXT_DISPLAY = 10
TYPE_SEPARATOR = 14
TYPE_SECTION = 9
TYPE_CONTAINER = 17
TYPE_ACTION_ROW = 1


def _layout_view() -> object:
    return (
        UILayout()
        .add(
            Container(accent=BLURPLE)
            .add(Text("# Title"))
            .add(Section(Text("body"), button=Button("PR", "https://example.com")))
            .add(Separator())
            .add(Row(Button("Pipeline", "https://example.com")))
            .add(Text("-# footer"))
        )
        .build()
    )


def test_layout_builds_a_components_v2_view() -> None:
    view = _layout_view()
    assert isinstance(view, object)
    from discord import ui as discord_ui

    assert isinstance(view, discord_ui.LayoutView)


def test_layout_builds_full_structure() -> None:
    view = _layout_view()
    components = view.to_components()  # type: ignore[attr-defined]
    assert len(components) == 1
    container = components[0]
    assert container["type"] == TYPE_CONTAINER
    kinds = [block["type"] for block in container["components"]]
    assert kinds == [
        TYPE_TEXT_DISPLAY,
        TYPE_SECTION,
        TYPE_SEPARATOR,
        TYPE_ACTION_ROW,
        TYPE_TEXT_DISPLAY,
    ]


def test_section_button_accessory_links() -> None:
    view = (
        UILayout()
        .add(Container().add(Section(Text("body"), button=Button("PR", "https://example.com"))))
        .build()
    )
    container = view.to_components()[0]  # type: ignore[attr-defined]
    section = next(b for b in container["components"] if b["type"] == TYPE_SECTION)
    accessory = section["accessory"]
    assert accessory["label"] == "PR"
    assert accessory["url"] == "https://example.com"
    assert accessory["style"] == 5  # link


def test_section_thumbnail_accessory() -> None:
    view = (
        UILayout()
        .add(Container().add(Section(Text("body"), thumbnail=Thumbnail("https://example.com/a.png"))))
        .build()
    )
    container = view.to_components()[0]  # type: ignore[attr-defined]
    section = next(b for b in container["components"] if b["type"] == TYPE_SECTION)
    assert section["accessory"]["media"]["url"] == "https://example.com/a.png"


def test_text_budget_is_enforced() -> None:
    big = "x" * 4001
    with pytest.raises(UILayoutError, match="budget"):
        (
            UILayout()
            .add(Container().add(Text(big)))
            .build()
        )


def test_section_requires_an_accessory() -> None:
    with pytest.raises(UILayoutError, match="accessory"):
        Section(Text("body"))


def test_section_rejects_two_accessories() -> None:
    with pytest.raises(UILayoutError, match="accessory"):
        Section(
            Text("body"),
            button=Button("PR", "https://example.com"),
            thumbnail=Thumbnail("https://example.com/a.png"),
        )


def test_section_rejects_more_than_three_texts() -> None:
    with pytest.raises(UILayoutError, match="text blocks"):
        Section(
            Text("a"),
            Text("b"),
            Text("c"),
            Text("d"),
            button=Button("PR", "https://example.com"),
        )


def test_row_rejects_empty() -> None:
    with pytest.raises(UILayoutError, match="buttons"):
        Row()


def test_row_rejects_more_than_five_buttons() -> None:
    buttons = [Button(str(i), "https://example.com") for i in range(6)]
    with pytest.raises(UILayoutError, match="buttons"):
        Row(*buttons)


def test_ui_embed_builds_with_budget_check() -> None:
    embed = (
        UIEmbed(title="Kingdoms — Status", color=BLURPLE, description="d")
        .field("Uptime", "1h 2m")
        .footer("kingdoms")
        .build()
    )
    assert embed.title == "Kingdoms — Status"
    assert embed.fields[0].name == "Uptime"
    with pytest.raises(UILayoutError, match="budget"):
        UIEmbed(title="t" * 3000, description="d" * 2000).build()
