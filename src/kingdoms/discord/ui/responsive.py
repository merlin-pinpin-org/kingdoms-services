"""The ONE rule for answering an interaction with a view (the view SDK).

Every view-rendering callback MUST answer through
:func:`respond_with_view` — never ``followup.send``/``send_message``
directly. The rule (owner direction, after the fifty-identical-panels
incident):

- the click rode a message (a component interaction): the message is
  UPDATED in place with the fresh view — same view id or not, the
  clicked surface never spawns a sibling; an ephemeral origin edits
  through its webhook (the ``ephemeral.py`` contract), a persistent
  origin through the interaction's own response;
- the click rode no message (a slash command, a fresh surface): the
  view is sent as an EPHEMERAL — an interaction never posts a
  persistent message.

This is the single choke point: the rule lives here, nowhere else.
"""

from __future__ import annotations

import logging
from typing import Any

import discord

from kingdoms.discord.ephemeral import EphemeralOrigin, edit_ephemeral

logger = logging.getLogger("kingdoms.discord.ui.responsive")

__all__ = ["ack_view_interaction", "respond_with_view"]


async def ack_view_interaction(interaction: discord.Interaction, *, thinking: bool = True) -> None:
    """Ack an interaction before slow work (the 3-second rule, one place).

    Discord kills an unacked interaction after 3 s and every later
    response 404s (merlin 10062). Every view callback doing slow work
    (a provider call, a DB read) acks through this — with
    ``thinking=True`` the user sees the bot working.
    """
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=thinking)


async def respond_with_view(
    interaction: discord.Interaction,
    view: Any,
    view_id: str = "",
) -> None:
    """Answer an interaction with a view — update the same id, else ephemeral.

    ``view_id`` is the view's declared id (``panel:game:aoe2:live``):
    logged for tracing; the routing is entirely carried by the message
    the interaction rode.

    Rules (see the module docstring): a component click updates the
    message it rode; a message-less interaction gets an ephemeral.
    """
    message = getattr(interaction, "message", None)
    if message is not None:
        # a component click: the clicked message is the surface —
        # update it, never spawn a sibling
        origin = EphemeralOrigin.capture(interaction)
        if origin is not None and int(getattr(message.flags, "value", 0) or 0) & (1 << 6):
            # an ephemeral message edits through its webhook
            if await edit_ephemeral(origin, view):
                # the deferred thinking message (ack_view_interaction)
                # is a residue — the clicked surface carries the fresh
                # view, exactly one message stays
                await _delete_deferred_response(interaction)
                return
            # the token expired (a >15 min old surface): fall through
            # to a fresh ephemeral rather than losing the answer
            logger.debug("ephemeral edit missed (token expired) — sending a fresh ephemeral")
            await _send_ephemeral(interaction, view)
            return
        # a persistent message (a pinned panel, a menu message): the
        # pin rules forbid editing the pin itself through the
        # interaction — but a NON-pinned menu message updates itself
        from kingdoms.discord.pinned_marks import is_pinned_view

        if not is_pinned_view(message):
            if not interaction.response.is_done():
                await interaction.response.edit_message(view=view)
                return
            try:
                await interaction.followup.edit_message(message.id, view=view)
                await _delete_deferred_response(interaction)
                return
            except Exception:
                logger.debug("in-place edit failed — answering as an ephemeral", exc_info=True)
        # a pinned message: its content updates through the dedicated
        # ensure cycle; the click answers with an ephemeral
        await _send_ephemeral(interaction, view)
        return
    # no message (a slash command): always an ephemeral
    await _send_ephemeral(interaction, view)


async def _delete_deferred_response(interaction: discord.Interaction) -> None:
    """Delete the deferred thinking message when the flow ends elsewhere.

    ``ack_view_interaction`` defers with thinking=True — that creates an
    ephemeral message; when the answer lands on the ORIGIN (edited in
    place), the thinking residue must go: one surface, the clicked one.
    """
    try:
        await interaction.delete_original_response()
    except Exception:
        logger.debug("deferred response cleanup skipped — best-effort", exc_info=True)


async def _send_ephemeral(interaction: discord.Interaction, view: Any) -> None:
    """Send the view as an ephemeral answer (deferred-safe)."""
    if interaction.response.is_done():
        await interaction.followup.send(view=view, ephemeral=True)
    else:
        await interaction.response.send_message(view=view, ephemeral=True)
