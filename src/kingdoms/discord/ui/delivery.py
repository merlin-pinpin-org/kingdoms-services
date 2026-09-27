"""Per-destination message rendering policy (kingdoms-services#56).

Two delivery contexts exist, and the audience defines the content
(``docs/architecture/discord-permissions.md``, ADR-0016):

- a **DM** has one known recipient: components the user lacks the
  role for are rendered **disabled** — visible, teaching what to
  unlock next — and components that can never apply to the user are
  omitted entirely;
- a **channel message** is a broadcast to everyone who can read the
  channel: only components declared ``public`` (no role requirement)
  are ever rendered;
- an **ephemeral** answer targets the interacting user: same content
  rules as a DM.

Rendering is a **UX hint only**: the security boundary stays the
click-time authorization of :mod:`kingdoms.discord.permissions` (#55).
The rules here mirror that runtime exactly — an open component with
``dm_allowed=False`` is omitted from a DM because the runtime would
deny its click there.

Components declare their delivery intent once
(:class:`ComponentPolicy`); mods never hand-write ``disabled=True``
per button. The policy is attached to the built ``discord.ui`` items
(:func:`annotate`) — by the SDK builders when a ``policy`` is passed,
or by :meth:`PermissionedView.apply_destination` for the view
archetypes — and :func:`render_for` applies it to both UI systems of
ADR-0009 (embed-views and Components V2 layouts, walking nested
children).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

import discord

__all__ = [
    "PUBLIC_POLICY",
    "AnyView",
    "ComponentPolicy",
    "MessageDestination",
    "annotate",
    "annotate_layout",
    "component_policies",
    "interactive_items",
    "render_for",
]

POLICY_ATTR = "_kingdoms_component_policy"

AnyView = discord.ui.View | discord.ui.LayoutView


class MessageDestination(Enum):
    """Where a message goes — the audience defines the content."""

    DM = "dm"
    CHANNEL = "channel"
    EPHEMERAL = "ephemeral"


@dataclass(frozen=True, slots=True)
class ComponentPolicy:
    """Delivery intent of one interactive component.

    ``required_roles`` are logical role keys (resolved through the
    mod-role mappings at click time); ``dm_allowed`` allows the
    component in a DM; ``public`` marks it safe for a channel
    broadcast. An unannotated component defaults to the open public
    policy — matching the core service's open-action default — except
    that it is not DM-allowed, mirroring the runtime DM rule.
    """

    required_roles: tuple[str, ...] = ()
    dm_allowed: bool = False
    public: bool = True

    @property
    def is_public(self) -> bool:
        """Whether a channel message may carry this component."""
        return self.public and not self.required_roles


PUBLIC_POLICY = ComponentPolicy()


def annotate(item: discord.ui.Item[Any], policy: ComponentPolicy) -> None:
    """Attach the delivery intent to a built ``discord.ui`` item.

    discord.py items are not slotted, so the annotation is a plain
    attribute next to the item; :func:`render_for` reads it back.
    """
    setattr(item, POLICY_ATTR, policy)


def annotate_layout(view: discord.ui.LayoutView, policies: dict[str, ComponentPolicy]) -> None:
    """Attach per-custom_id policies to an already-built layout."""
    for item in interactive_items(view).values():
        custom_id = str(getattr(item, "custom_id", "") or "")
        policy = policies.get(custom_id)
        if policy is not None:
            annotate(item, policy)


def component_policies(view: AnyView) -> dict[str, ComponentPolicy]:
    """Map every interactive component's custom_id to its policy."""
    return {custom_id: _policy_of(item) for custom_id, item in interactive_items(view).items()}


def interactive_items(view: AnyView) -> dict[str, discord.ui.Item[Any]]:
    """Every interactive item (custom_id present), V2 nests included."""
    items: dict[str, discord.ui.Item[Any]] = {}
    for child in view.children:
        candidates: list[discord.ui.Item[Any]] = [child]
        walker = getattr(child, "walk_children", None)
        if callable(walker):
            candidates.extend(walker())
        for item in candidates:
            custom_id = str(getattr(item, "custom_id", "") or "")
            if custom_id and custom_id not in items:
                items[custom_id] = item
    return items


def render_for(
    view: AnyView,
    destination: MessageDestination,
    user_roles: frozenset[str] | set[str] | tuple[str, ...] = (),
) -> AnyView:
    """Apply the destination policy to a view, in place.

    **DM / EPHEMERAL**: components whose required roles the user
    already holds stay usable; role-gated ``dm_allowed`` components
    the user lacks the role for are disabled; everything else the
    runtime would deny in a DM is omitted.

    **CHANNEL**: only public components survive; role-gated ones are
    removed from the view entirely.
    """
    roles = frozenset(user_roles)
    if destination in (MessageDestination.DM, MessageDestination.EPHEMERAL):
        _render_private(view, roles)
    else:
        _render_channel(view)
    return view


def _policy_of(item: discord.ui.Item[Any]) -> ComponentPolicy:
    policy = getattr(item, POLICY_ATTR, None)
    if isinstance(policy, ComponentPolicy):
        return policy
    return PUBLIC_POLICY


def _render_private(view: AnyView, roles: frozenset[str]) -> None:
    for item in list(interactive_items(view).values()):
        policy = _policy_of(item)
        satisfies = any(role in roles for role in policy.required_roles)
        if policy.required_roles and satisfies:
            continue
        if policy.required_roles:
            if policy.dm_allowed:
                item.disabled = True
            else:
                _remove(view, item)
            continue
        if policy.dm_allowed:
            continue
        _remove(view, item)
    _prune_empty_rows(view)


def _render_channel(view: AnyView) -> None:
    for item in list(interactive_items(view).values()):
        policy = _policy_of(item)
        if not policy.is_public:
            _remove(view, item)
    _prune_empty_rows(view)


def _remove(view: AnyView, item: discord.ui.Item[Any]) -> None:
    parent = getattr(item, "parent", None)
    if parent is not None and hasattr(parent, "remove_item"):
        parent.remove_item(item)
    else:
        view.remove_item(item)


def _prune_empty_rows(view: AnyView) -> None:
    for child in list(view.children):
        if isinstance(child, discord.ui.ActionRow) and not list(child.children):
            parent = getattr(child, "parent", None)
            if parent is not None and hasattr(parent, "remove_item"):
                parent.remove_item(child)
            else:
                view.remove_item(child)
