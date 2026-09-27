"""Discord rendering of the exception taxonomy (kingdoms-services#10).

A :class:`~kingdoms.core.exceptions.KingdomsError` reaching a Discord
interaction answers with an **ephemeral i18n message** selected by
the error's ``user_key`` (never a raw traceback to a player) and
audits the failure to the guild's bot-logs; unexpected exceptions
keep the crash-report path of :mod:`kingdoms.discord.error_report`
(#113) — the developer still needs the frame.

The runtime permission denial (#55) is **not** an error path: it is
a deliberate decision with its own message and audit, and never
raises.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from kingdoms.core.exceptions import ExceptionHandler, KingdomsError

if TYPE_CHECKING:
    from kingdoms.core.services.i18n import MessageCatalog
    from kingdoms.core.services.logs import LogService

logger = logging.getLogger("kingdoms.bot.errors")

FALLBACK_LOCALE = "en"


async def answer_kingdoms_error(
    interaction: discord.Interaction,
    error: KingdomsError,
    catalog: MessageCatalog,
    logs_service: LogService | None = None,
    locale: str = FALLBACK_LOCALE,
) -> bool:
    """Answer a Kingdoms error ephemerally and audit it to bot-logs.

    Returns whether the interaction was answered (the caller falls
    back to the crash-report path when it was not). Best-effort by
    design: a failing error path never masks the original failure.
    """
    ExceptionHandler.log(error)
    message = catalog.render(error.user_key, locale=locale)
    answered = False
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
        answered = True
    except Exception:
        logger.warning("ERROR ANSWER FAILED — best-effort", exc_info=error)
    if logs_service is not None and interaction.guild_id is not None:
        try:
            from kingdoms.core.services.logs import LifecycleEvent

            event = LifecycleEvent(
                kind="error",
                message=f"**{error.code}**: {error.message}",
            )
            await logs_service.log_event(str(interaction.guild_id), event)
        except Exception:
            logger.warning("ERROR AUDIT FAILED — best-effort", exc_info=error)
    return answered
