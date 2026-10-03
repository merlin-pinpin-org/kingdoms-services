"""Registration DM workflow: bind a user to a validated game profile (kingdoms-services#133).

Stateless executor driven by the WorkflowEngine (ADR-0002): each step
returns the next transition; persistence is the engine's. The flow:

``entry`` → confirm the enrollment request → ``profile`` → the user types
their in-game id (3-second rule: reply within the step timeout) → the
RegistrationService validates and binds → ``done``.

Events carry the step payload; the engine persists it so the flow
survives restarts by construction.
"""

from __future__ import annotations

from typing import Any

from kingdoms.core.exceptions.workflow import WorkflowError
from kingdoms.core.interfaces.platform import IWorkflow
from kingdoms.core.models.workflow import WorkflowStatus, WorkflowTransition
from kingdoms.core.services.registration import InvalidProfileError, ProfileBoundError, RegistrationService

STEP_ENTRY = "entry"
STEP_PROFILE = "profile"
STEP_DONE = "done"

STATUS_PENDING = "PENDING"


class RegistrationWorkflow(IWorkflow):
    """DM enrollment + game-profile binding, one step per interaction."""

    def __init__(self, registration: RegistrationService, game_key: str = "aoe2") -> None:
        """Wire the registration service and the target game."""
        self._registration = registration
        self._game_key = game_key

    @property
    def name(self) -> str:
        """Unique workflow name (registration key for the engine)."""
        return "registration"

    def steps(self) -> list[str]:
        """Ordered step names; the first is the entry step."""
        return [STEP_ENTRY, STEP_PROFILE, STEP_DONE]

    async def start(self, context: dict[str, Any]) -> WorkflowTransition:
        """Open the flow at the entry step, pending the confirm interaction."""
        return WorkflowTransition(
            current_step=STEP_ENTRY,
            status=WorkflowStatus.IN_PROGRESS,
            payload={"game_key": self._game_key, **context},
        )

    async def handle_interaction(self, state: WorkflowTransition, event: dict[str, Any]) -> WorkflowTransition:
        """Advance one step per interaction; the envelope carries the step data."""
        action = event.get("action")
        if state.current_step == STEP_ENTRY:
            if action != "confirm":
                return await self._cancel(state, "entry_declined")
            return WorkflowTransition(
                current_step=STEP_PROFILE,
                status=WorkflowStatus.IN_PROGRESS,
                payload=dict(state.payload),
            )
        if state.current_step == STEP_PROFILE:
            profile_id = str(event.get("profile_id", "")).strip()
            if not profile_id:
                return WorkflowTransition(
                    current_step=STEP_PROFILE,
                    status=WorkflowStatus.IN_PROGRESS,
                    payload={**state.payload, "error": "empty_profile_id"},
                )
            try:
                binding = await self._registration.bind_profile(
                    event["user_id"], state.payload["game_key"], profile_id
                )
            except InvalidProfileError:
                return WorkflowTransition(
                    current_step=STEP_PROFILE,
                    status=WorkflowStatus.IN_PROGRESS,
                    payload={**state.payload, "error": "invalid_profile"},
                )
            except ProfileBoundError:
                return WorkflowTransition(
                    current_step=STEP_PROFILE,
                    status=WorkflowStatus.IN_PROGRESS,
                    payload={**state.payload, "error": "profile_taken"},
                )
            return WorkflowTransition(
                current_step=STEP_DONE,
                status=WorkflowStatus.COMPLETED,
                payload={**state.payload, "binding": binding["_id"]},
            )
        raise WorkflowError(f"unexpected step {state.current_step!r}")

    async def on_timeout(self, state: WorkflowTransition) -> WorkflowTransition:
        """Cancel a timed-out DM flow (the user can restart it)."""
        return await self._cancel(state, "timeout")

    async def _cancel(self, state: WorkflowTransition, reason: str) -> WorkflowTransition:
        """Return the cancelled transition with the reason in the payload."""
        return WorkflowTransition(
            current_step=state.current_step,
            status=WorkflowStatus.CANCELLED,
            payload={**state.payload, "cancel_reason": reason},
        )
