"""Interactive views: buttons and selects on embed-based messages.

Per ADR-0009 these attach to **embed-based** messages; rich structured
UI belongs to Components V2 (``factory.py``). Custom IDs follow the
repo convention ``<mod>:<component>:<payload>`` and every callback
flows through :func:`kingdoms.discord.permissions.require_permission`
at click time — the runtime checks (#55) are the security boundary,
not the visibility of the button.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import discord

from kingdoms.discord.ui.delivery import (
    ComponentPolicy,
    MessageDestination,
    annotate,
    render_for,
)

__all__ = [
    "ConfirmationView",
    "GameSelectionView",
    "PaginationView",
]

Handler = Callable[[discord.Interaction], Awaitable[None]]
PageHandler = Callable[[discord.Interaction, int], Awaitable[None]]
SelectHandler = Callable[[discord.Interaction, str], Awaitable[None]]


class PermissionedView(discord.ui.View):
    """Base view: click-time permission checks on every interaction.

    ``permission_args(mod, custom_id)`` returns the authorization
    inputs for one component (``(required_roles, dm_allowed)``); the
    default declares no required role — open to every guild member —
    matching the core service's open-action default.
    """

    def __init__(
        self,
        permission_service: Any = None,
        catalog: Any = None,
        logs_service: Any = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._permission_service = permission_service
        self._catalog = catalog
        self._logs_service = logs_service

    def permission_args(self, mod: str, custom_id: str) -> tuple[tuple[str, ...], bool]:
        """Return (required_roles, dm_allowed) declared for one component."""
        return (), False

    def apply_destination(
        self,
        destination: MessageDestination,
        user_roles: frozenset[str] | set[str] | tuple[str, ...] = (),
    ) -> PermissionedView:
        """Apply the #56 delivery policy to this view's components.

        Components annotated at construction (their declared intent)
        are disabled or omitted per the destination — the rendering
        mirror of the click-time checks.
        """
        render_for(self, destination, user_roles)
        return self

    @property
    def mod(self) -> str:
        """The mod this view's components belong to (custom_id prefix)."""
        return "core"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Authorize the click at click time (fail-open only when unwired)."""
        if self._permission_service is None:
            return True
        from kingdoms.discord.permissions import require_permission

        data: dict[str, Any] = interaction.data if isinstance(interaction.data, dict) else {}  # type: ignore[assignment]
        custom_id = str(data.get("custom_id", "") or "")
        required_roles, dm_allowed = self.permission_args(self.mod, custom_id)
        return await require_permission(
            interaction,
            self._permission_service,
            mod=self.mod,
            required_roles=required_roles,
            dm_allowed=dm_allowed,
            catalog=self._catalog,
            logs_service=self._logs_service,
        )


class ConfirmationView(PermissionedView):
    """A Yes/No confirmation pair, wired through the permission checks."""

    def __init__(
        self,
        mod: str = "core",
        on_confirm: Handler | None = None,
        on_cancel: Handler | None = None,
        permission_service: Any = None,
        catalog: Any = None,
        logs_service: Any = None,
        confirm_label: str = "Yes",
        cancel_label: str = "No",
        timeout: float = 180.0,
        required_roles: tuple[str, ...] = (),
        dm_allowed: bool = False,
    ) -> None:
        super().__init__(
            permission_service=permission_service, catalog=catalog, logs_service=logs_service, timeout=timeout
        )
        self._mod = mod
        self._required_roles = required_roles
        self._dm_allowed = dm_allowed
        self._on_confirm = on_confirm
        self._on_cancel = on_cancel
        self.confirm_button: discord.ui.Button[Any] = discord.ui.Button(
            style=discord.ButtonStyle.success, label=confirm_label, custom_id=f"{mod}:confirm:yes"
        )
        self.cancel_button: discord.ui.Button[Any] = discord.ui.Button(
            style=discord.ButtonStyle.danger, label=cancel_label, custom_id=f"{mod}:confirm:no"
        )
        self.confirm_button.callback = self._confirm  # type: ignore[method-assign]
        self.cancel_button.callback = self._cancel  # type: ignore[method-assign]
        policy = ComponentPolicy(
            required_roles=required_roles,
            dm_allowed=dm_allowed,
            public=not required_roles,
        )
        annotate(self.confirm_button, policy)
        annotate(self.cancel_button, policy)
        self.add_item(self.confirm_button)
        self.add_item(self.cancel_button)

    @property
    def mod(self) -> str:
        """The mod this view's components belong to."""
        return self._mod

    def permission_args(self, mod: str, custom_id: str) -> tuple[tuple[str, ...], bool]:
        """Every button of the confirmation pair declares the same roles."""
        return self._required_roles, self._dm_allowed

    async def _confirm(self, interaction: discord.Interaction) -> None:
        """Dispatch the confirmed branch."""
        if self._on_confirm is not None:
            await self._on_confirm(interaction)

    async def _cancel(self, interaction: discord.Interaction) -> None:
        """Dispatch the cancelled branch."""
        if self._on_cancel is not None:
            await self._on_cancel(interaction)


class GameSelectionView(PermissionedView):
    """A select menu over a game list, dispatching the chosen value."""

    def __init__(
        self,
        games: list[str],
        mod: str = "core",
        on_select: SelectHandler | None = None,
        permission_service: Any = None,
        catalog: Any = None,
        logs_service: Any = None,
        placeholder: str = "Select a game...",
        timeout: float = 180.0,
        required_roles: tuple[str, ...] = (),
        dm_allowed: bool = False,
    ) -> None:
        super().__init__(
            permission_service=permission_service, catalog=catalog, logs_service=logs_service, timeout=timeout
        )
        self._mod = mod
        self._required_roles = required_roles
        self._dm_allowed = dm_allowed
        self._on_select = on_select
        self.game_select: discord.ui.Select[Any] = discord.ui.Select(
            placeholder=placeholder,
            options=[discord.SelectOption(label=game, value=game) for game in games],
            custom_id=f"{mod}:game:select",
        )
        self.game_select.callback = self._selected  # type: ignore[method-assign]
        annotate(
            self.game_select,
            ComponentPolicy(required_roles=required_roles, dm_allowed=dm_allowed, public=not required_roles),
        )
        self.add_item(self.game_select)

    @property
    def mod(self) -> str:
        """The mod this view's components belong to."""
        return self._mod

    def permission_args(self, mod: str, custom_id: str) -> tuple[tuple[str, ...], bool]:
        """Declare the same roles as the view for the select."""
        return self._required_roles, self._dm_allowed

    async def _selected(self, interaction: discord.Interaction) -> None:
        """Dispatch the first chosen value."""
        if self._on_select is None:
            return
        values = list(self.game_select.values or [])
        if values:
            await self._on_select(interaction, str(values[0]))


class PaginationView(PermissionedView):
    """Previous/Next buttons editing the message in place, page-aware."""

    def __init__(
        self,
        total_pages: int,
        current_page: int = 0,
        mod: str = "core",
        on_page_change: PageHandler | None = None,
        permission_service: Any = None,
        catalog: Any = None,
        logs_service: Any = None,
        timeout: float = 300.0,
        prev_label: str = "Previous",
        next_label: str = "Next",
    ) -> None:
        super().__init__(
            permission_service=permission_service, catalog=catalog, logs_service=logs_service, timeout=timeout
        )
        self._mod = mod
        self.total_pages = total_pages
        self.current_page = current_page
        self._on_page_change = on_page_change
        self.previous_button: discord.ui.Button[Any] = discord.ui.Button(
            style=discord.ButtonStyle.secondary,
            label=prev_label,
            custom_id=f"{mod}:page:prev",
            disabled=current_page <= 0,
        )
        self.next_button: discord.ui.Button[Any] = discord.ui.Button(
            style=discord.ButtonStyle.secondary,
            label=next_label,
            custom_id=f"{mod}:page:next",
            disabled=current_page >= total_pages - 1,
        )
        self.page_counter: discord.ui.Button[Any] = discord.ui.Button(
            style=discord.ButtonStyle.secondary,
            label=f"{current_page + 1}/{total_pages}",
            custom_id=f"{mod}:page:counter",
            disabled=True,
        )
        self.previous_button.callback = self._previous  # type: ignore[method-assign]
        self.next_button.callback = self._next  # type: ignore[method-assign]
        policy = ComponentPolicy(dm_allowed=True, public=True)
        annotate(self.previous_button, policy)
        annotate(self.next_button, policy)
        annotate(self.page_counter, policy)
        self.add_item(self.previous_button)
        self.add_item(self.page_counter)
        self.add_item(self.next_button)

    @property
    def mod(self) -> str:
        """The mod this view's components belong to."""
        return self._mod

    def update_buttons(self, current_page: int) -> None:
        """Refresh the disabled states and the page counter label."""
        self.current_page = current_page
        self.previous_button.disabled = current_page <= 0
        self.next_button.disabled = current_page >= self.total_pages - 1
        self.page_counter.label = f"{current_page + 1}/{self.total_pages}"

    async def _previous(self, interaction: discord.Interaction) -> None:
        """Move one page back and edit in place."""
        if self.current_page > 0:
            self.update_buttons(self.current_page - 1)
            if self._on_page_change is not None:
                await self._on_page_change(interaction, self.current_page)
        await interaction.response.edit_message(view=self)

    async def _next(self, interaction: discord.Interaction) -> None:
        """Move one page forward and edit in place."""
        if self.current_page < self.total_pages - 1:
            self.update_buttons(self.current_page + 1)
            if self._on_page_change is not None:
                await self._on_page_change(interaction, self.current_page)
        await interaction.response.edit_message(view=self)
