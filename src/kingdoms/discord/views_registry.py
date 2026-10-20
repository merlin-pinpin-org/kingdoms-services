"""The views registry: every menu and panel, declared once.

Two kinds of views, one vocabulary:

- a **menu** navigates: buttons only, each opening another view
  (``menu:home`` → ``menu:games:home``). No data, no actions;
- a **panel** informs or acts: data sections, action buttons,
  live content (``panel:profile``, ``panel:game:aoe2:live``).

Every view carries a **view id** (``menu:games:aoe2``,
``panel:game:aoe2:players``) rendered as its footer — the anchor
every human feedback refers to. The registry is the single source of
truth the doc (docs/DEVELOPMENT/VIEWS.md in the kingdoms repo) and
the builders stay consistent with; a view built but not declared here
(or declared but never built) is a drift the tests catch.

Constraints baked in:

- every view declares its kind, its channel scope (where its pin
  lives) and its buttons' targets — all registered ids, checked;
- an admin button renders with the padlock emoji 🔒, as discreet as
  Discord allows (secondary style, no label weight);
- a sync button (channels & roles provisioning) renders with the gear emoji;
- the registry caps what the platform carries: 20 games, 20 mods.
"""

from __future__ import annotations

from dataclasses import dataclass, field

ADMIN_EMOJI = "\U0001f512"
SYNC_EMOJI = "\u2699\ufe0f"

MAX_GAMES = 20
MAX_MODS = 20

KIND_MENU = "menu"
KIND_PANEL = "panel"


@dataclass(frozen=True, slots=True)
class ButtonDecl:
    """One button of a view: a label key and its target view id."""

    target: str
    label_key: str = ""
    admin: bool = False
    sync: bool = False
    disabled: bool = False


@dataclass(frozen=True, slots=True)
class ViewDecl:
    """One declared view: its id, kind, scope and buttons."""

    id: str
    kind: str
    scope: str = ""
    buttons: tuple[ButtonDecl, ...] = field(default_factory=tuple)


_REGISTRY: dict[str, ViewDecl] = {}


def register_view(decl: ViewDecl) -> ViewDecl:
    """Declare one view (idempotent per id, last wins)."""
    _REGISTRY[decl.id] = decl
    return decl


def get_view(view_id: str) -> ViewDecl | None:
    """Return one declared view."""
    return _REGISTRY.get(view_id)


def registered_views() -> tuple[ViewDecl, ...]:
    """Return every declared view, in registration order."""
    return tuple(_REGISTRY.values())


def declared_targets(decl: ViewDecl) -> tuple[str, ...]:
    """Return the view ids a view's buttons open (empty for panels)."""
    return tuple(b.target for b in decl.buttons if b.target)


def validate_registry() -> list[str]:
    """Return the registry's drifts (unknown targets), empty when clean."""
    drifts: list[str] = []
    for decl in _REGISTRY.values():
        for target in declared_targets(decl):
            if target not in _REGISTRY:
                drifts.append(f"{decl.id} opens undeclared view {target}")
        for button in decl.buttons:
            if decl.kind == KIND_MENU and not button.target and not button.sync and not button.label_key:
                drifts.append(f"{decl.id}: a menu button needs a target, the sync flag or a label")
    return drifts


def clear_registry() -> None:
    """Empty the registry (tests only)."""
    _REGISTRY.clear()


def default_button_emoji(button: ButtonDecl) -> str:
    """Return a button's emoji: padlock for admin, update for sync."""
    if button.admin:
        return ADMIN_EMOJI
    if button.sync:
        return SYNC_EMOJI
    return ""


__all__ = [
    "ADMIN_EMOJI",
    "KIND_MENU",
    "KIND_PANEL",
    "MAX_GAMES",
    "MAX_MODS",
    "SYNC_EMOJI",
    "ButtonDecl",
    "ViewDecl",
    "clear_registry",
    "declared_targets",
    "default_button_emoji",
    "get_view",
    "register_view",
    "registered_views",
    "validate_registry",
]
