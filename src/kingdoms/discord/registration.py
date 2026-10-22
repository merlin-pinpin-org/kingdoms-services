"""The /register command and the DM enrollment flow (kingdoms-services#133).

Registration is a *permanent* mod: the flow binds the Discord user to a
validated AoE2 profile. The command opens the DM workflow: confirm →
type the in-game profile id → the RegistrationService validates through
the game seam (ext-librematch via svc-core) and binds. Steps ride the
WorkflowEngine so the flow survives restarts; the views are dynamic
items (restart-proof, #122) and answers follow the 3-second rule.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import discord
from discord import app_commands

from kingdoms.core.services.i18n import MessageCatalog
from kingdoms.core.services.registration import RegistrationService
from kingdoms.core.services.workflow import WorkflowEngine
from kingdoms.discord.commands_i18n import reply

logger = logging.getLogger("kingdoms.registration")

CONFIRM_PREFIX = "registration:confirm"
PROFILE_MODAL = "registration:profile"


class RegistrationConfirmButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"registration:confirm:(?P<choice>accept|decline)",
):
    """Restart-proof confirm button: the choice rides the custom_id."""

    def __init__(self, choice: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="Confirm enrollment" if choice == "accept" else "Cancel",
                style=discord.ButtonStyle.primary if choice == "accept" else discord.ButtonStyle.secondary,
                custom_id=f"registration:confirm:{choice}",
            )
        )
        self.choice = choice

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> RegistrationConfirmButton:
        """Rebuild the button from the wire after a restart."""
        return cls(match["choice"])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Advance the workflow one step (confirm)."""
        engine, registration = _wiring()
        if engine is None:
            await interaction.response.send_message(await reply(interaction, "unavailable"), ephemeral=True)
            return
        workflow_id = _active_workflow_id(interaction.user.id)
        if workflow_id is None:
            await interaction.response.send_message(
                "Aucune inscription en cours — recommence avec /register.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        if self.choice == "decline":
            await engine.cancel(workflow_id)
            await interaction.followup.send("Enrollment cancelled — /register whenever you like.", ephemeral=True)
            return
        state = await engine.handle_interaction(workflow_id, {"action": "confirm", "user_id": str(interaction.user.id)})
        if state.status in ("CANCELLED", "TIMED_OUT"):
            await interaction.followup.send("Enrollment cancelled.", ephemeral=True)
            return
        del registration
        await interaction.followup.send(
            view=_profile_prompt_view(), content="Almost there — what is your AoE2 profile id?", ephemeral=True
        )


class ProfileIdModal(discord.ui.Modal, title="AoE2 profile"):
    """The profile-id step: one text input, the 3-second rule applies."""

    profile_id: discord.ui.TextInput[Any] = discord.ui.TextInput(
        label="Your AoE2 profile id",
        placeholder="e.g. 123456",
        min_length=1,
        max_length=32,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Validate and bind through the RegistrationService."""
        engine, _ = _wiring()
        if engine is None:
            await interaction.response.send_message(await reply(interaction, "unavailable"), ephemeral=True)
            return
        workflow_id = _active_workflow_id(interaction.user.id)
        if workflow_id is None:
            await interaction.response.send_message(
                "Aucune inscription en cours — recommence avec /register.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        state = await engine.handle_interaction(
            workflow_id,
            {"action": "profile", "user_id": str(interaction.user.id), "profile_id": str(self.profile_id)},
        )
        error = state.payload.get("error")
        if error:
            await interaction.followup.send(render_profile_error(str(error)), ephemeral=True)
            return
        if state.status == "COMPLETED":
            _clear_active(interaction.user.id)
            await interaction.followup.send(
                f"Inscription terminée ! Ton profil AoE2 `{state.payload.get('binding', '')}` est lié — tu peux rejoindre le ladder.",  # noqa: E501
                ephemeral=True,
            )
            return
        await interaction.followup.send("État inattendu — recommence avec /register.", ephemeral=True)


def _profile_prompt_view() -> discord.ui.View:
    """Build the profile step view: one button opening the modal."""

    class OpenProfileModal(discord.ui.Button[discord.ui.View]):
        """Button opening the AoE2 profile-id modal."""

        def __init__(self) -> None:
            super().__init__(label="Enter my profile id", style=discord.ButtonStyle.primary)

        async def callback(self, interaction: discord.Interaction) -> None:
            """Open the profile-id modal on click."""
            await interaction.response.send_modal(ProfileIdModal())

    view = discord.ui.View(timeout=None)
    view.add_item(OpenProfileModal())
    return view


_ACTIVE: dict[int, str] = {}
_WIRING: tuple[WorkflowEngine | None, RegistrationService | None] = (None, None)


def set_registration_wiring(engine: WorkflowEngine | None, registration: RegistrationService | None) -> None:
    """Provide the engine and service to the dynamic items (factory hook)."""
    global _WIRING
    _WIRING = (engine, registration)


def _wiring() -> tuple[WorkflowEngine | None, RegistrationService | None]:
    return _WIRING


def _active_workflow_id(user_id: int) -> str | None:
    return _ACTIVE.get(user_id)


def _clear_active(user_id: int) -> None:
    _ACTIVE.pop(user_id, None)


def register_registration_command(
    tree: app_commands.CommandTree[discord.Client],
    engine: WorkflowEngine | None,
    registration: RegistrationService | None,
    catalog: MessageCatalog | None = None,
    bot: discord.Client | None = None,
) -> None:
    """Register the persistent confirm buttons (no /register command).

    The profile binding lives in the home profile menu since 2026-10-16
    (/home > Profil) — the DM enrollment workflow and its confirm buttons
    stay for the sessions that still ride them.

    ``engine``/``registration`` may be None in local runs — the command
    then answers with a note instead of failing.
    """
    set_registration_wiring(engine, registration)
    if bot is not None:
        bot.add_dynamic_items(RegistrationConfirmButton)


_ERROR_MESSAGES = {
    "empty_profile_id": "The profile id cannot be empty — try again.",
    "invalid_profile": "That profile id is unknown to the AoE2 provider — check it and try again.",
    "profile_taken": "That profile is already linked to another player.",
}


def render_profile_error(error: str) -> str:
    """Map a workflow payload error to the user-facing message."""
    return _ERROR_MESSAGES.get(error, "Validation failed — try again.")


def build_enrollment_screen(user: dict[str, Any]) -> str:
    """Render the enrollment confirmation text (i18n-neutral, used in DM)."""
    return f"Enrollment for <@{user.get('user_id', '')}> — confirm to bind your AoE2 profile."
