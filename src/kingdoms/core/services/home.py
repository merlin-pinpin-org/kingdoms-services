"""Core home surface: the guild's pinned entry menu, one button per view.

The home is the guild's front door (kingdoms-services#133 follow-up): a
pinned menu in a dedicated channel, plus the ``/home`` command showing
the same menu. Every button opens an **ephemeral** view — the home is
public, the answers are personal.

The core owns the *shape* (which standard buttons exist, how a mod
contributes its own) and stays platform-agnostic:

- standard buttons: ``status``, ``profile``, ``games``, ``users`` and
  ``admin`` (the last one guarded at click time);
- every **enabled mod** contributes one button through its declaration
  (``home:`` in ``config/mods/<mod>.yaml``): a mod that declares a home
  key gets a button; a mod that declares none is simply absent;
- a mod button opens the mod's own home view — the mod defines *what*
  it shows (its surface), the core only routes the click.

Reference: §0/§6 (the core never imports platform code), ADR-0020.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

HOME_MARKER = "home:pin:"
HOME_BUTTON_IDS = ("status", "profile", "games", "users", "admin")


@dataclass(frozen=True, slots=True)
class HomeButton:
    """One home button: a view key, a label and an emoji."""

    view: str
    label: str
    emoji: str


class HomeRegistry(Protocol):
    """Narrow seam on the mod registry (the enabled-mod declarations)."""

    def enabled(self) -> dict[str, Any]:
        """Every enabled mod definition, by name."""
        ...


class ModHomeViewProvider(Protocol):
    """Narrow seam: the Discord wiring provides one view builder per mod."""

    def mod_home_view(self, mod: str) -> Any | None:
        """Return the mod's home view builder; None when the mod has none."""
        ...


class HomeService:
    """Compose the home menu buttons: the standards + one per enabled mod."""

    def __init__(self, registry: HomeRegistry, mod_views: ModHomeViewProvider | None = None) -> None:
        self._registry = registry
        self._mod_views = mod_views

    def standard_buttons(self) -> list[HomeButton]:
        """Return the platform's standard home buttons, in display order."""
        return [
            HomeButton(view="status", label="Status", emoji="🧭"),
            HomeButton(view="profile", label="Profile", emoji="👤"),
            HomeButton(view="games", label="Games", emoji="🎮"),
            HomeButton(view="users", label="Users", emoji="👥"),
            HomeButton(view="admin", label="Admin", emoji="🛡"),
        ]

    def mod_buttons(self) -> list[HomeButton]:
        """One button per enabled mod that provides a home view."""
        buttons: list[HomeButton] = []
        for name in sorted(self._registry.enabled()):
            if self._mod_views is None or self._mod_views.mod_home_view(name) is None:
                continue
            buttons.append(HomeButton(view=f"mod:{name}", label=name.capitalize(), emoji="🧩"))
        return buttons

    def buttons(self) -> list[HomeButton]:
        """Every home button: the standards, then the mods'."""
        return [*self.standard_buttons(), *self.mod_buttons()]

    def mod_of_view(self, view: str) -> str | None:
        """Return the mod name a ``mod:<name>`` view key refers to; None otherwise."""
        if view.startswith("mod:"):
            return view[4:]
        return None
