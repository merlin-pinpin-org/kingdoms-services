"""The entity admin menu's SDK seam: the default lifecycle, disable/override.

The SDK composes the bar (the rows, the ``panel:<id>:admin`` footer
ALWAYS last); the caller hands the buttons in, disables a key, or
overrides the matrix — the designer's visibility rules, by design.
"""

from __future__ import annotations

from typing import Any

import discord

from botforge.discord.ui.entity_admin import (
    ARCHIVE,
    DEFAULT_LIFECYCLE,
    DELETE,
    EDIT,
    PUBLISH,
    EntityAdminSpec,
    admin_panel_id,
    build_admin_bar,
    lifecycle_row,
)


def _button(key: str) -> Any:
    return discord.ui.Button(label=key, custom_id=f"test:{key}")


def _texts(view: Any) -> list[str]:
    out: list[str] = []

    def _visit(node: Any) -> None:
        content = getattr(node, "content", None)
        if isinstance(content, str):
            out.append(content)
        for child in getattr(node, "children", ()) or ():
            _visit(child)

    for child in view.children:
        _visit(child)
    return out


def test_the_default_lifecycle_shows_per_state_hidden_never_greyed() -> None:
    assert DEFAULT_LIFECYCLE["draft"] == (EDIT, PUBLISH, ARCHIVE)
    assert DEFAULT_LIFECYCLE["published"] == (EDIT, ARCHIVE)
    assert DEFAULT_LIFECYCLE["closed"] == (EDIT, PUBLISH, DELETE)


def test_the_lifecycle_row_composes_the_state_actions_in_order() -> None:
    spec = EntityAdminSpec(
        entity_id="map_pool:aoe2:p1",
        state="closed",
        actions={key: _button(key) for key in (EDIT, PUBLISH, ARCHIVE, DELETE)},
    )
    row = lifecycle_row(spec)
    assert row is not None
    labels = [b.label for b in row.children]
    assert labels == [EDIT, PUBLISH, DELETE]


def test_a_disabled_key_never_renders() -> None:
    spec = EntityAdminSpec(
        entity_id="map_pool:aoe2:p1",
        state="closed",
        actions={key: _button(key) for key in (EDIT, PUBLISH, DELETE)},
        disabled=frozenset({DELETE}),
    )
    row = lifecycle_row(spec)
    assert row is not None
    assert [b.label for b in row.children] == [EDIT, PUBLISH]


def test_the_bar_carries_the_panel_admin_footer_last() -> None:
    spec = EntityAdminSpec(
        entity_id="map_pool:aoe2:p1",
        state="draft",
        actions={key: _button(key) for key in (EDIT, PUBLISH, ARCHIVE)},
    )
    view = build_admin_bar(spec)
    texts = _texts(view)
    assert texts[-1] == "-# `panel:map_pool:aoe2:p1:admin`"
    assert admin_panel_id("map_pool:aoe2:p1") == "panel:map_pool:aoe2:p1:admin"


def test_the_compose_rows_ride_before_the_lifecycle() -> None:
    compose: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    compose.add_item(_button("maps"))
    spec = EntityAdminSpec(
        entity_id="map_pool:aoe2:p1",
        state="draft",
        actions={key: _button(key) for key in (EDIT, PUBLISH, ARCHIVE)},
    ).with_compose((compose,))
    view = build_admin_bar(spec)
    rows = [i for i in view.children if isinstance(i, discord.ui.ActionRow)]
    assert [b.label for b in rows[0].children] == ["maps"]
    assert [b.label for b in rows[1].children] == [EDIT, PUBLISH, ARCHIVE]
