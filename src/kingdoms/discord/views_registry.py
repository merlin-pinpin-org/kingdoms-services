"""The views registry: every menu and panel, declared once.

Two kinds of views, one vocabulary:

- a **menu** navigates: buttons only, each opening another view
  (``menu:home`` → ``menu:games:home``). No data, no actions;
- a **panel** informs or acts: data sections, action buttons,
  live content (``panel:profile``, ``panel:game:aoe2:live``).

Every view carries a **view id** (``menu:games:aoe2``,
``panel:game:aoe2:players``) rendered as its footer — the anchor
every human feedback refers to. The registry loads from ``config/views.yaml`` (the single source
of truth): the runtime menu builder, the pin registrations and the
button router all render from it, and the kingdoms repo's
views-doc page renders it for humans. A view built but not declared
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
    """One button of a view: a navigation target or a wired action.

    Exactly one of ``target`` (opens the declared view), ``sync`` (the
    channels & roles ensure) or ``action`` (a behavior wired at boot
    through ``wire_action``) drives the click; a declared-but-unwired
    action renders disabled — never a dead button.
    """

    target: str = ""
    label_key: str = ""
    admin: bool = False
    sync: bool = False
    disabled: bool = False
    action: str = ""


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
            drivers = sum(1 for d in (button.target, button.sync, button.action) if d)
            if drivers > 1:
                drifts.append(
                    f"{decl.id}: a button is target, sync or action — never two"
                )
            if drivers == 0:
                drifts.append(f"{decl.id}: a button needs a target, the sync flag or an action")
            if button.action and not button.label_key:
                drifts.append(f"{decl.id}: the action button {button.action!r} needs a label")
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
