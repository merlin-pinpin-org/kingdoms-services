"""Unit tests for the UI screen archetypes (kingdoms.discord.ui.screens).

Archetypes (Ranking, config panel, match report, pagination) are the
ready-made screen shapes features instantiate with data instead of
layout code. These tests pin their structure and the pagination
contract: pages are pre-built blocks, Prev/Next actions edit the
message in place, and every archetype funnels through the factory so
the build-time guarantees hold.
"""

from __future__ import annotations

import pytest

from kingdoms.discord.ui import (
    PaginatedScreen,
    Ranking,
    UILayoutError,
    build_config_panel,
    build_match_report,
    render_ranking,
)

TYPE_TEXT_DISPLAY = 10
TYPE_SEPARATOR = 14
TYPE_ACTION_ROW = 1
TYPE_SELECT = 3


def _texts(view) -> list[str]:
    out: list[str] = []

    def walk(components: list) -> None:
        for c in components:
            if c.get("type") == TYPE_TEXT_DISPLAY:
                out.append(c["content"])
            walk(c.get("components", []))

    walk(view.to_components())
    return out


def test_ranking_pages_split_entries_with_podium() -> None:
    ranking = Ranking("Classement", [(f"p{i}", str(100 - i)) for i in range(12)])
    pages = ranking.pages(page_size=5)
    assert len(pages) == 3
    first = "\n".join(str(b.content) for b in pages[0] if hasattr(b, "content"))
    assert "🥇" in first and "🥈" in first and "🥉" in first
    assert "#4" in first
    second = "\n".join(str(b.content) for b in pages[1] if hasattr(b, "content"))
    assert "#6" in second and "🥇" not in second


def test_ranking_requires_entries() -> None:
    with pytest.raises(UILayoutError, match="at least one entry"):
        Ranking("Vide", [])


def test_render_ranking_is_a_paginated_screen() -> None:
    ranking = Ranking("Top", [("alice", "10"), ("bob", "8")])
    screen = render_ranking(ranking)
    assert isinstance(screen, PaginatedScreen)
    assert len(screen.pages) == 1
    view = screen.render()
    joined = "\n".join(_texts(view))
    assert "🏆 Top" in joined
    assert "alice" in joined
    assert "Page 1/1" in joined


def test_paginated_screen_multi_page_has_prev_next_only_where_valid() -> None:
    pages = [[], [], []]
    pages[0] = [__import__("kingdoms.discord.ui", fromlist=["Text"]).Text("one")]
    from kingdoms.discord.ui import Text

    pages[1] = [Text("two")]
    pages[2] = [Text("three")]
    screen = PaginatedScreen(pages, mod="ranking", title="Test")
    first = screen.render()
    rows_first = [c for c in first.to_components()[0]["components"] if c.get("type") == TYPE_ACTION_ROW]
    labels_first = [b["label"] for r in rows_first for b in r["components"]]
    assert labels_first == ["Next ▶"]
    screen.current = 2
    last = screen.render()
    rows_last = [c for c in last.to_components()[0]["components"] if c.get("type") == TYPE_ACTION_ROW]
    labels_last = [b["label"] for r in rows_last for b in r["components"]]
    assert labels_last == ["◀ Prev"]


@pytest.mark.asyncio
async def test_paginated_screen_next_edits_in_place() -> None:
    from kingdoms.discord.ui import Text

    screen = PaginatedScreen([[Text("one")], [Text("two")]], mod="ranking", title="Test")

    class FakeResponse:
        def __init__(self) -> None:
            self.edited_with: object | None = None

        async def edit_message(self, **kwargs: object) -> None:
            self.edited_with = kwargs.get("view")

    class FakeInteraction:
        def __init__(self) -> None:
            self.response = FakeResponse()

    interaction = FakeInteraction()
    await screen.on_next(interaction)  # type: ignore[arg-type]
    assert screen.current == 1
    assert interaction.response.edited_with is not None
    joined = "\n".join(_texts(interaction.response.edited_with))
    assert "two" in joined


def test_config_panel_structure() -> None:
    from kingdoms.discord.ui import Text  # noqa: F401  (archetype builds rows)

    async def on_apply(interaction: object) -> None:
        return None

    async def on_reset(interaction: object) -> None:
        return None

    async def on_change(interaction: object, values: list[str]) -> None:
        return None

    panel = build_config_panel(
        "Réglages",
        [
            {
                "key": "lang",
                "label": "Langue",
                "value": "fr",
                "options": [("Français", "fr"), ("English", "en")],
                "on_change": on_change,
            }
        ],
        mod="cfg",
        on_apply=on_apply,
        on_reset=on_reset,
    )
    joined = "\n".join(_texts(panel))
    assert "⚙️ Réglages" in joined
    components = panel.to_components()[0]["components"]
    select_ids = [
        s["custom_id"]
        for c in components
        if c.get("type") == TYPE_ACTION_ROW
        for s in c["components"]
        if s.get("type") == TYPE_SELECT
    ]
    assert select_ids == ["cfg:setting:lang"]
    button_ids = [
        b["custom_id"]
        for c in components
        if c.get("type") == TYPE_ACTION_ROW
        for b in c["components"]
        if b.get("type") == 2
    ]
    assert button_ids == ["cfg:config:apply", "cfg:config:reset"]


def test_match_report_structure() -> None:
    report = build_match_report(
        "Match 42",
        "**Rouges 3 - 2 Bleus**",
        [("MVP", "alice"), ("Durée", "42m")],
        mod="game",
    )
    joined = "\n".join(_texts(report))
    assert "⚔️ Match 42" in joined
    assert "Rouges 3 - 2 Bleus" in joined
    assert "**MVP** alice" in joined
