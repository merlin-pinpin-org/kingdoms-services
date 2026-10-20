"""The declared home menu pins through the static-pin machinery."""
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
