"""Tests for the core home service: the standard buttons + one per mod.

The home is the guild's front door: the core owns the shape (the five
standard buttons, how a mod contributes its own) and stays
platform-agnostic — a mod gets a button only when the wiring provides
its home view builder.
"""
from __future__ import annotations

from typing import Any

from kingdoms.core.services.home import HomeService


class _FakeRegistry:
    """Mod registry stand-in: the enabled mods by name."""

    def __init__(self, *names: str) -> None:
        self._names = names

    def enabled(self) -> dict[str, Any]:
        return {name: object() for name in self._names}


class _FixedProvider:
    """Mod-home provider stand-in: a builder for the listed mods only."""

    def __init__(self, *mods: str) -> None:
        self._mods = set(mods)

    def mod_home_view(self, mod: str) -> Any | None:
        return object() if mod in self._mods else None


def test_standard_buttons_are_the_five_platform_views() -> None:
    """The standards are exactly status/profile/games/users/admin, in order."""
    home = HomeService(_FakeRegistry())
    assert [b.view for b in home.standard_buttons()] == ["status", "profile", "games", "users", "admin"]


def test_buttons_include_only_mods_with_a_home_view() -> None:
    """A mod appears only when the wiring provides its home view builder."""
    home = HomeService(_FakeRegistry("ladder", "groups"), _FixedProvider("ladder"))
    views = [b.view for b in home.buttons()]
    assert "mod:ladder" in views
    assert "mod:groups" not in views
    assert views.index("mod:ladder") > views.index("admin")


def test_mod_of_view_routes_mod_keys_only() -> None:
    """mod_of_view decodes mod:<name> keys and rejects the standards."""
    home = HomeService(_FakeRegistry())
    assert home.mod_of_view("mod:ladder") == "ladder"
    assert home.mod_of_view("status") is None
