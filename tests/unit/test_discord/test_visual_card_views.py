"""Unit tests for the visual card button views.

Pins the wire contract (#122): the entity rides the custom_id, the
items rebuild from it after a restart, and the public row carries the
source link plus the admin drawer toggle.
"""

from __future__ import annotations

import pytest

from kingdoms.discord.visual_card_views import (
    _NS,
    CardAdminButton,
    CardAdminView,
    CardRegenerateButton,
    card_view,
)


def test_card_view_rows_and_custom_ids():
    view = card_view("faction", "faction:aoe2:Franks", source_url="https://aoe2techtree.net")
    buttons = list(view.children)
    assert len(buttons) == 2
    source, admin = buttons
    assert source.style.value == 5  # link style
    assert source.url == "https://aoe2techtree.net"
    assert isinstance(admin, CardAdminButton)
    assert admin.custom_id == f"{_NS}:admin:faction:faction:aoe2:Franks"


def test_admin_button_custom_id_rebuilds():
    btn = CardAdminButton("map", "map:aoe2:arabia")
    match = btn.template.fullmatch(btn.custom_id)
    assert match is not None
    assert match.group("kind") == "map"
    assert match.group("payload") == "map:aoe2:arabia"


def test_regen_button_custom_id_rebuilds():
    btn = CardRegenerateButton("faction", "faction:aoe2:Goths")
    match = btn.template.fullmatch(btn.custom_id)
    assert match is not None
    assert match.group("payload") == "faction:aoe2:Goths"


def test_admin_drawer_holds_the_admin_actions():
    view = CardAdminView("faction", "faction:aoe2:Franks")
    assert any(isinstance(item, CardRegenerateButton) for item in view.children)


@pytest.mark.asyncio
async def test_from_custom_id_reconstruction():
    item = await CardAdminButton.from_custom_id(
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        CardAdminButton("map", "map:aoe2:arabia").template.fullmatch(f"{_NS}:admin:map:map:aoe2:arabia"),
    )
    assert isinstance(item, CardAdminButton)
    assert item.kind == "map"
    assert item.payload == "map:aoe2:arabia"
