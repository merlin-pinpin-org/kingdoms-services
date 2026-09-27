"""UI modals: forms (modal dialogs) collecting structured user input.

Custom IDs follow ``<mod>:<component>:<payload>``. Modals ride on the
interaction that opened them — the runtime permission check happens
before the modal opens (the view or command guards it), not at submit.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import discord

__all__ = [
    "FeedbackModal",
    "RegistrationModal",
]

SubmitHandler = Callable[[discord.Interaction, dict[str, str]], Awaitable[None]]


class RegistrationModal(discord.ui.Modal):
    """Registration form: display name + preferred game."""

    def __init__(self, title: str = "Register", on_submit: SubmitHandler | None = None, timeout: float = 300.0) -> None:
        super().__init__(title=title, timeout=timeout)
        self.name_input: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label="Your Name",
            placeholder="Enter your name...",
            custom_id="registration:name",
            min_length=2,
            max_length=50,
        )
        self.game_input: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label="Preferred Game",
            placeholder="aoe2, chess, etc.",
            custom_id="registration:game",
            min_length=2,
            max_length=50,
        )
        self.add_item(self.name_input)
        self.add_item(self.game_input)
        self._on_submit = on_submit

    def set_callback(self, callback: SubmitHandler) -> None:
        """Set the submit handler."""
        self._on_submit = callback

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Dispatch the collected fields."""
        if self._on_submit is not None:
            data = {"name": self.name_input.value, "game": self.game_input.value}
            await self._on_submit(interaction, data)

    async def on_error(self, interaction: discord.Interaction, error: Exception, /) -> None:  # type: ignore[override]
        """Answer submission failures ephemerally (never silent)."""
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "An error occurred while processing your registration. Please try again.",
                ephemeral=True,
            )


class FeedbackModal(discord.ui.Modal):
    """Feedback form: type + free-form details."""

    def __init__(
        self, title: str = "Submit Feedback", on_submit: SubmitHandler | None = None, timeout: float = 300.0
    ) -> None:
        super().__init__(title=title, timeout=timeout)
        self.type_input: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label="Type",
            placeholder="bug, feature, suggestion, etc.",
            custom_id="feedback:type",
            min_length=2,
            max_length=50,
        )
        self.details_input: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label="Details",
            placeholder="Describe your feedback...",
            custom_id="feedback:details",
            style=discord.TextStyle.paragraph,
            min_length=10,
            max_length=2000,
        )
        self.add_item(self.type_input)
        self.add_item(self.details_input)
        self._on_submit = on_submit

    def set_callback(self, callback: SubmitHandler) -> None:
        """Set the submit handler."""
        self._on_submit = callback

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Dispatch the collected fields."""
        if self._on_submit is not None:
            data: dict[str, str] = {"type": self.type_input.value, "details": self.details_input.value}
            await self._on_submit(interaction, data)

    async def on_error(self, interaction: discord.Interaction, error: Exception, /) -> None:  # type: ignore[override]
        """Answer submission failures ephemerally (never silent)."""
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "An error occurred while processing your feedback. Please try again.",
                ephemeral=True,
            )
