"""Discord-side runtime authorization for interactive components.

The core :class:`~kingdoms.core.services.permissions.PermissionService`
decides; this module answers. ``require_permission`` is the single
hook every component callback goes through at click time: on denial
the user gets the ephemeral i18n reason and an audit event rides to
the bot-logs channel (ADR-0003) — a denial never fails silently
(kingdoms-services#55).
"""

from __future__ import annotations

import logging
from typing import Any

import discord

from kingdoms.core.services.i18n import MessageCatalog
from kingdoms.core.services.logs import LifecycleEvent, LogService
from kingdoms.core.services.permissions import (
    PERMISSION_DENIED_KEY,
    ActionContext,
    PermissionResult,
    PermissionService,
)

logger = logging.getLogger("kingdoms.discord.permissions")


def _default_denied_message() -> str:
    """Return the built-in English denial (guards.denied wording)."""
    return "You are not allowed to do that — this action is reserved for bot admins."


def _context_from_interaction(
    interaction: discord.Interaction,
    mod: str,
    required_roles: tuple[str, ...],
    dm_allowed: bool,
) -> ActionContext:
    """Build the ActionContext from a live interaction (fail-loud custom_id)."""
    raw: Any = interaction.data
    data: dict[str, Any] = raw if isinstance(raw, dict) else {}
    custom_id = str(data.get("custom_id", "") or "")
    if not custom_id:
        custom_id = f"{mod}:interaction:"
    return ActionContext(
        user_id=str(getattr(interaction.user, "id", "")),
        mod=mod,
        custom_id=custom_id,
        guild_id=str(interaction.guild_id) if interaction.guild_id else None,
        required_roles=required_roles,
        dm_allowed=dm_allowed,
    )


async def require_permission(
    interaction: discord.Interaction,
    service: PermissionService,
    mod: str,
    required_roles: tuple[str, ...] = (),
    dm_allowed: bool = False,
    catalog: MessageCatalog | None = None,
    logs_service: LogService | None = None,
    locale: str = "en",
) -> bool:
    """Guard one interactive callback at click time.

    Returns True when the interaction may proceed; on denial the user
    gets an ephemeral localized message and the denial is audited to
    the guild's bot-logs channel. The caller must return immediately
    on False.
    """
    ctx = _context_from_interaction(interaction, mod, required_roles, dm_allowed)
    result: PermissionResult = await service.is_authorized(ctx)
    if result.allowed:
        return True
    message = _denied_message(catalog, locale)
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except Exception:
        logger.warning("DENIAL ANSWER FAILED — best-effort", exc_info=True)
    await _audit_denial(logs_service, ctx, result, message)
    return False


def _denied_message(catalog: MessageCatalog | None, locale: str) -> str:
    """Render the localized denial (permissions.denied → guards.denied)."""
    if catalog is not None:
        try:
            return catalog.render(PERMISSION_DENIED_KEY, locale)
        except Exception:
            logger.warning("DENIED MESSAGE RENDER FAILED — built-in English", exc_info=True)
    return _default_denied_message()


async def _audit_denial(
    logs_service: LogService | None,
    ctx: ActionContext,
    result: PermissionResult,
    message: str,
) -> None:
    """Audit one denial to the guild's bot-logs (best-effort)."""
    if logs_service is None or ctx.guild_id is None:
        return
    try:
        event = LifecycleEvent(
            kind="permission_denied",
            message=(f"Permission denied: <@{ctx.user_id}> tried `{ctx.custom_id}` ({ctx.mod}) — {result.reason}"),
        )
        await logs_service.log_event(ctx.guild_id, event)
    except Exception:
        logger.warning("DENIAL AUDIT FAILED — best-effort", exc_info=True)
