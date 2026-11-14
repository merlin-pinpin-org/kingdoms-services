"""The entity admin menu: the default lifecycle, disableable and overridable.

**The SDK's purity rule (developer-mandated): the discord SDK never
queries a service and never reads data.** It receives everything
(buttons, ids, state, rows) and builds; the consumer (the bot layer)
owns every read. A module under ``botforge/discord/ui/`` that
imports a service or fetches an entity is a bug — the architecture
guards reject the import, the review rejects the fetch.

The SDK is a LIB used by the bot layer (and tomorrow by any surface
through the gRPC seam): it owns the SHAPE — the ephemeral admin bar's
action rows and its ``panel:<entity_id>:admin`` footer, ALWAYS the
last line — and the DEFAULT lifecycle (edit, publish, archive,
delete) with its per-state visibility matrix. The bot layer owns the
buttons themselves (guards, custom ids, callbacks): it hands the
button instances in, the SDK composes.

Every default is a seam (owner direction, 2026-11-13):

* **disable** — an action key in ``disabled`` never renders;
* **override** — the caller's button instance for a key wins (the
  SDK never inspects it);
* **extend** — extra rows ride before the lifecycle row, extra keys
  ride the matrix's own order.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import discord

EDIT = "edit"
PUBLISH = "publish"
ARCHIVE = "archive"
DELETE = "delete"

# The default lifecycle's visibility matrix: which actions show per
# state (hidden, never greyed — the designer's visibility rule). A
# state absent from the matrix shows nothing.
DEFAULT_LIFECYCLE: dict[str, tuple[str, ...]] = {
    "draft": (EDIT, PUBLISH, ARCHIVE),
    "published": (EDIT, ARCHIVE),
    "closed": (EDIT, PUBLISH, DELETE),
}

PANEL_PREFIX = "panel"


@dataclass(frozen=True)
class EntityAdminSpec:
    """One entity's admin bar: the actions, their policy, the extras."""

    entity_id: str
    actions: Mapping[str, Any] = field(default_factory=dict)
    state: str = ""
    disabled: frozenset[str] = frozenset()
    matrix: dict[str, tuple[str, ...]] = field(default_factory=lambda: dict(DEFAULT_LIFECYCLE))
    extra_rows: tuple[discord.ui.ActionRow[discord.ui.LayoutView], ...] = ()
    compose_rows: tuple[discord.ui.ActionRow[discord.ui.LayoutView], ...] = ()

    def with_compose(
        self, rows: tuple[discord.ui.ActionRow[discord.ui.LayoutView], ...]
    ) -> EntityAdminSpec:
        """Return the spec with the compose rows (the builder-style chain)."""
        return EntityAdminSpec(
            entity_id=self.entity_id,
            actions=self.actions,
            state=self.state,
            disabled=self.disabled,
            matrix=self.matrix,
            extra_rows=self.extra_rows,
            compose_rows=rows,
        )

    def visible_actions(self) -> tuple[Any, ...]:
        """Resolve the lifecycle row's buttons.

        The state's keys, minus the disabled, in the matrix's order —
        an omitted key (no button handed in) never renders.
        """
        keys = self.matrix.get(self.state, ()) if self.state else tuple(self.actions)
        return tuple(
            self.actions[key]
            for key in keys
            if key not in self.disabled and key in self.actions
        )


def lifecycle_row(spec: EntityAdminSpec) -> discord.ui.ActionRow[discord.ui.LayoutView] | None:
    """Build the lifecycle row from the spec (None when empty)."""
    buttons = spec.visible_actions()
    if not buttons:
        return None
    row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    for button in buttons:
        row.add_item(button)
    return row


def build_admin_bar(spec: EntityAdminSpec) -> discord.ui.LayoutView:
    """Build the ephemeral admin bar: the rows, the panel footer last.

    The bar's view id follows the platform's prefix conventions
    (``panel:dm:admin``, ``panel:<kind>:<name>`` — mods_forum): the
    entity admin panel is ``panel:<entity_id>:admin`` — a VIEW id,
    distinct from the resource id, never a bare ``<id>:admin``.
    """
    view = discord.ui.LayoutView(timeout=None)
    for row in (*spec.compose_rows, *spec.extra_rows):
        view.add_item(row)
    lifecycle = lifecycle_row(spec)
    if lifecycle is not None:
        view.add_item(lifecycle)
    view.add_item(discord.ui.TextDisplay(f"-# `{PANEL_PREFIX}:{spec.entity_id}:admin`"))
    return view


def admin_panel_id(entity_id: str) -> str:
    """Return the entity's admin panel view id (the footer's anchor)."""
    return f"{PANEL_PREFIX}:{entity_id}:admin"


__all__ = [
    "ARCHIVE",
    "DEFAULT_LIFECYCLE",
    "DELETE",
    "EDIT",
    "PUBLISH",
    "EntityAdminSpec",
    "admin_panel_id",
    "build_admin_bar",
    "lifecycle_row",
]
