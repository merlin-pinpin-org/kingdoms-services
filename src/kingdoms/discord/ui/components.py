"""Reusable component builders for common UI patterns.

Thin, consistent wrappers over the declarative SDK bricks
(:mod:`kingdoms.discord.ui.factory`) — the single place features
should pick a button or a select from. Interactive buttons carry the
mod's required roles so the runtime permission checks (#55) can
authorize the click; the visibility of a button is never the boundary.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from kingdoms.discord.ui.factory import Action, Option, SelectMenu

__all__ = [
    "ButtonBuilder",
    "SelectBuilder",
]

Handler = Callable[[Any], Awaitable[None]]
SelectHandler = Callable[[Any, list[str]], Awaitable[None]]


class ButtonBuilder:
    """Builder for interactive buttons with consistent styling.

    Every interactive button declares its mod and the logical role
    keys required to click it — the ``required_roles`` flow to the
    runtime permission check at click time.
    """

    @staticmethod
    def primary(
        label: str,
        custom_id: str,
        on_click: Handler,
        mod: str = "core",
        required_roles: tuple[str, ...] = (),
        emoji: str = "",
        dm_allowed: bool = False,
    ) -> Action:
        """Create a primary (blue) action button."""
        return Action(label, custom_id, on_click, emoji=emoji, style="primary")

    @staticmethod
    def success(
        label: str,
        custom_id: str,
        on_click: Handler,
        mod: str = "core",
        required_roles: tuple[str, ...] = (),
        emoji: str = "",
        dm_allowed: bool = False,
    ) -> Action:
        """Create a success (green) action button."""
        return Action(label, custom_id, on_click, emoji=emoji, style="success")

    @staticmethod
    def danger(
        label: str,
        custom_id: str,
        on_click: Handler,
        mod: str = "core",
        required_roles: tuple[str, ...] = (),
        emoji: str = "",
        dm_allowed: bool = False,
    ) -> Action:
        """Create a danger (red) action button."""
        return Action(label, custom_id, on_click, emoji=emoji, style="danger")

    @staticmethod
    def secondary(
        label: str,
        custom_id: str,
        on_click: Handler,
        mod: str = "core",
        required_roles: tuple[str, ...] = (),
        emoji: str = "",
        dm_allowed: bool = False,
    ) -> Action:
        """Create a secondary (grey) action button."""
        return Action(label, custom_id, on_click, emoji=emoji, style="secondary")


class SelectBuilder:
    """Builder for select menus with consistent styling."""

    @staticmethod
    def single_select(
        custom_id: str,
        placeholder: str,
        options: list[dict[str, str]],
        on_choose: SelectHandler,
        min_values: int = 1,
        max_values: int = 1,
    ) -> SelectMenu:
        """Create a single-selection select menu."""
        return SelectBuilder._build(custom_id, placeholder, options, on_choose, min_values, max_values)

    @staticmethod
    def multi_select(
        custom_id: str,
        placeholder: str,
        options: list[dict[str, str]],
        on_choose: SelectHandler,
        min_values: int = 1,
        max_values: int = 5,
    ) -> SelectMenu:
        """Create a multi-selection select menu."""
        return SelectBuilder._build(custom_id, placeholder, options, on_choose, min_values, max_values)

    @staticmethod
    def _build(
        custom_id: str,
        placeholder: str,
        options: list[dict[str, str]],
        on_choose: SelectHandler,
        min_values: int,
        max_values: int,
    ) -> SelectMenu:
        """Assemble a SelectMenu from loose option dicts."""
        parsed = tuple(
            Option(
                label=str(o.get("label", "")),
                value=str(o.get("value", "")),
                description=str(o.get("description", "")),
                emoji=str(o.get("emoji", "")),
            )
            for o in options
        )
        return SelectMenu(
            custom_id=custom_id,
            options=parsed,
            on_choose=on_choose,
            placeholder=placeholder,
            min_values=min_values,
            max_values=max_values,
        )
