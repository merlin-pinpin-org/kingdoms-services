"""The declared home menu pins through the static-pin machinery."""
from typing import Any

import pytest

from kingdoms.discord.views_declared import declare_views_from_config
from kingdoms.discord.views_registry import clear_registry


@pytest.fixture(autouse=True)
def _fresh_registry() -> None:
    clear_registry()
    declare_views_from_config("config")


@pytest.mark.asyncio
async def test_the_root_home_menu_pins_as_a_declared_view() -> None:
    """The root/general pin builds through the declared menu builder."""
    from unittest.mock import MagicMock

    from kingdoms.discord.scope_pins import register_scope_pins
    from kingdoms.discord.static_pins import reset_static_pins

    reset_static_pins()
    bot = MagicMock()
    specs = register_scope_pins(bot, ("root", "bot"))
    keys = {spec.key for spec in specs}
    assert any("home" in k for k in keys), "the root home pin registers"
    assert any("admin" in k for k in keys), "the bot admin pin registers"
    root_home = next(s for s in specs if "home" in s.key)
    assert root_home.legacy_key == "view:home", "the declared pin edits the legacy message in place"
    layout = await root_home.build_layout("42")
    customs = [c.custom_id for c in layout.walk_children() if getattr(c, "custom_id", None)]
    assert "view:open:panel:profile" in customs, "the declared buttons ride the pin"


def test_roster_lines_sorted_with_mention_and_links() -> None:
    """The roster lines: pseudo, mention, aoe2insights links — alphabetical."""
    from kingdoms.discord.home import _casefold_key, _roster_lines

    class _Member:
        def __init__(self, name: str) -> None:
            self.display_name = name

    class _Guild:
        def get_member(self, uid: int) -> Any:
            return {"10": _Member("Zoé"), "20": _Member("alice"), "30": _Member("Bob")}.get(str(uid))

    by_user = {
        "10": [{"user_id": "10", "profile_id": "111", "profile": {"display_name": "ZoeAoe"}}],
        "20": [{"user_id": "20", "profile_id": "222", "profile": {"display_name": "AliceAoe"}}],
        "30": [{"user_id": "30", "profile_id": "333", "profile": {"display_name": "BobAoe"}}],
    }
    lines = _roster_lines("aoe2", by_user, _Guild())
    assert lines[0].startswith("• **alice** — <@20>")
    assert lines[1].startswith("• **Bob** — <@30>")
    assert lines[2].startswith("• **Zoé** — <@10>")
    assert "https://www.aoe2insights.com/user/relic/222/" in lines[0]
    assert _casefold_key("Zoé") == "zoe"


def test_paginate_lines_splits_on_budget() -> None:
    """Lines beyond the budget split into several pages, none over budget."""
    from kingdoms.discord.home import _paginate_lines

    lines = [f"player-{i} " + "x" * 90 for i in range(10)]
    pages = _paginate_lines(lines, 200)
    assert len(pages) > 1
    assert all(len(page) <= 200 for page in pages)
    assert pages[0].startswith("player-0")
    assert pages[-1].endswith("x" * 90 or lines[-1].split()[-1])
