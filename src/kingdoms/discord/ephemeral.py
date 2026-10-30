"""Editing ephemeral views in place — the interaction webhook contract.

An ephemeral message is NOT editable through the REST channel route
(``PATCH /channels/{id}/messages/{id}`` answers 404 Unknown Message):
Discord exposes it only through the interaction webhook that created
it. The pattern (the skill rule "an ephemeral view edits itself"):

1. the click that opens the flow captures the pair
   ``(interaction.webhook, interaction.message.id)`` — the click's
   webhook token lives 15 minutes after the click, plenty for a
   modal round-trip;
2. the submit (or the deferred callback) edits the ORIGINAL message
   through ``webhook.edit_message(message_id, view=...)``.

``EphemeralOrigin`` carries that pair; ``edit_ephemeral`` performs
the edit, best-effort. A direct click that answers with
``interaction.response.edit_message`` needs none of this — the
current interaction's own webhook does it. This helper is for the
two deferred shapes: a modal submit, and a callback that had to
``defer(thinking=False)`` (whose response is a new ephemeral, not the
menu's).
"""

from __future__ import annotations

import logging
from typing import Any

import discord

logger = logging.getLogger("kingdoms.discord.ephemeral")


class EphemeralOrigin:
    """The (webhook, message id) pair able to edit one ephemeral menu."""

    def __init__(self, webhook: Any, message_id: int) -> None:
        self.webhook = webhook
        self.message_id = message_id

    @classmethod
    def capture(cls, interaction: discord.Interaction) -> EphemeralOrigin | None:
        """Capture the origin of the menu the click happened on.

        None when the click did not ride a message (a slash command,
        a fresh ephemeral without a parent) — the callers fall back
        to their plain-text answer.
        """
        message = getattr(interaction, "message", None)
        webhook = getattr(interaction, "webhook", None)
        if message is None or webhook is None:
            return None
        return cls(webhook, int(message.id))


async def edit_ephemeral(origin: EphemeralOrigin | None, view: Any) -> bool:
    """Edit the origin's ephemeral message with a fresh view.

    True when the edit landed; False (best-effort, logged) when the
    origin is missing or its webhook token expired — the caller then
    answers as plain text, NEVER a copy of the view.
    """
    if origin is None:
        return False
    try:
        await origin.webhook.edit_message(origin.message_id, view=view)
        return True
    except Exception:
        logger.warning("ephemeral edit in place failed — answering as text", exc_info=True)
        return False


__all__ = ["EphemeralOrigin", "edit_ephemeral"]
