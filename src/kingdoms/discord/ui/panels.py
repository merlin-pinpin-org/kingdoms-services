"""Panels SDK: the declarative contract for a guild's pinned menus.

A panel is a *frozen* surface: one pinned message per channel whose
page never changes. Its buttons open ephemeral views (navigation,
sub-menus, modals) — never in-place page swaps. The SDK owns the
whole lifecycle so a mod or a game never hand-rolls provisioning:

* ``PanelSpec`` declares the panel (name, channels, mode, heal,
  permissions, builder) — one dataclass, registered at boot;
* the provisioning applies the channel-tree rules
  (docs/architecture/channel-tree.md): per-scope folders, canonical
  order (général → discussions → data → admin → décisions),
  read-only menu channels, guild-locale naming;
* autopin + autoheal: the pin is (re)created on the delete events
  and by the safety sweep; the registry key is
  ``view:<guild-id>:<slug>`` so the pin id survives reboots.

The single-Admin-button rule lives here too: public surfaces never
carry action buttons; they carry one ⚙️ Admin button whose callback
checks permissions and opens a state-greyed ephemeral management
menu (see ``admin_button``).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import discord


class PinMode(Enum):
    """How a panel's message is pinned in its channel."""

    LATEST_ONLY = "latest_only"
    ALL = "all"
    NONE = "none"


@dataclass(frozen=True)
class ChannelDecl:
    """One channel of a scope folder, tree-rule-declared.

    ``key`` is the stable resolution key (the heal matches on it,
    never on the translated name); ``label_key`` is the i18n key of
    the displayed name; ``kind`` picks text/forum/category;
    ``access`` picks public (everyone), readonly (read for all, no
    send for anyone), or admin (bot-admins only).
    """

    key: str
    label_key: str
    kind: str = "text"
    access: str = "public"
    position: int | None = None


@dataclass(frozen=True)
class PanelSpec:
    """One frozen pinned menu, tree-rule-declared."""

    name: str
    channel: str
    build: Callable[[str, str], Awaitable[discord.ui.LayoutView]]
    pin_mode: PinMode = PinMode.LATEST_ONLY
    autoheal: bool = True
    permissions: tuple[str, ...] = ()
    description_key: str = ""

    @property
    def registry_key(self) -> str:
        """The message-registry key: ``view:<guild-id>:<slug>``."""
        from kingdoms.core.ids import slug_id

        return f"view:{slug_id(self.name)}"


@dataclass(frozen=True)
class ScopeSpec:
    """One scope folder (a game or a mod) and its panels.

    The canonical order is applied by the provisioning from the
    declarations: général, discussions, data channels, admin,
    décisions — ``position`` breaks ties inside a band.
    """

    scope: str
    category_key: str
    channels: tuple[ChannelDecl, ...] = field(default_factory=tuple)
    panels: tuple[PanelSpec, ...] = field(default_factory=tuple)


_REGISTRY: dict[str, ScopeSpec] = {}


def register_scope(spec: ScopeSpec) -> ScopeSpec:
    """Register one scope folder (idempotent; last wins)."""
    _REGISTRY[spec.scope] = spec
    return spec


def registered_scopes() -> tuple[ScopeSpec, ...]:
    """List every registered scope folder."""
    return tuple(_REGISTRY.values())


def admin_button(
    entity_id: str,
    kind: str,
    *,
    game_key: str = "",
    owned_by_guild: bool = False,
) -> discord.ui.Button[Any]:
    """Build the single ⚙️ Admin button of a public post.

    The tree rule: a public surface (a forum post) never carries
    action buttons — one Admin button opens the ephemeral management
    menu, permission-checked at click time, its buttons greyed by
    the entity's state. ``kind`` picks the management view
    (``map``, ``pool``, ``civ``).
    """
    import discord as _discord

    return _discord.ui.Button(
        label="Admin",
        emoji="\u2699",
        style=_discord.ButtonStyle.secondary,
        custom_id=f"panel:admin:{kind}:{entity_id}:{game_key}:{1 if owned_by_guild else 0}"[:100],
    )


__all__ = [
    "ChannelDecl",
    "PanelSpec",
    "PinMode",
    "ScopeSpec",
    "admin_button",
    "register_scope",
    "registered_scopes",
]
