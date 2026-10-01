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
from kingdoms.discord.commands_i18n import localized

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
            await interaction.response.send_message("Registration is unavailable.", ephemeral=True)
            return
        workflow_id = _active_workflow_id(interaction.user.id)
        if workflow_id is None:
            await interaction.response.send_message(
                "No enrollment in progress — start again with /register.", ephemeral=True
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
            await interaction.response.send_message("Registration is unavailable.", ephemeral=True)
            return
        workflow_id = _active_workflow_id(interaction.user.id)
        if workflow_id is None:
            await interaction.response.send_message(
                "No enrollment in progress — start again with /register.", ephemeral=True
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
                f"Enrolled! Your AoE2 profile `{state.payload.get('binding', '')}` is linked — you can join the ladder.",  # noqa: E501
                ephemeral=True,
            )
            return
        await interaction.followup.send("Unexpected flow state — restart with /register.", ephemeral=True)


def _profile_prompt_view() -> discord.ui.View:
    """Build the profile step view: one button opening the modal."""

    class OpenProfileModal(discord.ui.Button[discord.ui.View]):
        def __init__(self) -> None:
            super().__init__(label="Enter my profile id", style=discord.ButtonStyle.primary)

        async def callback(self, interaction: discord.Interaction) -> None:
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
    """Register /register and the persistent confirm buttons.

    ``engine``/``registration`` may be None in local runs — the command
    then answers with a note instead of failing.
    """
    set_registration_wiring(engine, registration)
    if bot is not None:
        bot.add_dynamic_items(RegistrationConfirmButton)

    @tree.command(
        name=localized("commands.register_name", "register"),
        description=localized(
            "commands.register_description", "Link your AoE2 profile to your account (DM enrollment)"
        ),
    )
    async def register_command(interaction: discord.Interaction) -> None:
        """Start the DM enrollment workflow."""
        if engine is None or registration is None:
            await interaction.response.send_message("Registration is unavailable on this deployment.", ephemeral=True)
            return
        existing = await registration.get_binding(str(interaction.user.id), "aoe2")
        if existing is not None:
            await interaction.response.send_message(
                f"You are already enrolled — profile `{existing.get('profile_id')}` is linked.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        workflow_id = await engine.start_workflow(
            "registration",
            guild_id=str(interaction.guild_id or "dm"),
            user_id=str(interaction.user.id),
            context={"user_id": str(interaction.user.id)},
        )
        _ACTIVE[interaction.user.id] = workflow_id
        dm = await interaction.user.create_dm()
        view = discord.ui.View(timeout=None)
        view.add_item(RegistrationConfirmButton("accept"))
        view.add_item(RegistrationConfirmButton("decline"))
        await dm.send(
            content=(
                "🏰 **Kingdoms enrollment**\n"
                "Link your AoE2 profile to join the ladder.\n\n"
                "Confirm to continue, or cancel. You can restart anytime with /register."
            ),
            view=view,
        )
        await interaction.followup.send("I sent you a DM — confirm your enrollment there.", ephemeral=True)


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
