"""Runtime permission guards: admin access is validated at click time.

Developer-mandated rule (kingdoms-services#109, #115): **never assume
that seeing a button means being allowed to click it**. Discord
components are visible to whoever can read the channel — permissions
drift, role removals and channel moves happen between render and
click. Every interactive item behind a privilege is validated **at
click time**, against the live guild state:

- **bot operators** — the ``BOT_ADMINS`` ids (environment-sourced);
- **guild administrators** — the administrator/manage-guild Discord
  permissions of the invoking member;
- **the ``bot-admins`` role** — the guild role provisioned by the
  RolesService (kingdoms-services#115), granted by the guild owners
  to delegated operators.

The guard answers a single question — "may this interaction act?" —
and is the one place every component callback and command handler
calls before doing anything privileged. A denial is answered
ephemerally with the reason; the caller never continues on denial.
"""

from __future__ import annotations

import logging

import discord

from kingdoms.core.services.roles import RolesService

logger = logging.getLogger("kingdoms.discord.guards")

BOT_ADMINS_ROLE_KEY = "bot-admins"

DENIED_MESSAGE = "You are not allowed to do that — this action is reserved for bot admins."


def _member_role_ids(interaction: discord.Interaction) -> set[int]:
    """Read the live role ids of the invoking member (empty outside a guild)."""
    member = getattr(interaction, "user", None)
    guild = getattr(interaction, "guild", None)
    if member is None or guild is None:
        return set()
    return {role.id for role in getattr(member, "roles", [])}


def is_bot_admin(user_id: int | None, bot_admins: tuple[str, ...]) -> bool:
    """Whether the invoking user is a bot operator (BOT_ADMINS)."""
    if user_id is None:
        return False
    return str(user_id) in bot_admins


def is_guild_admin(interaction: discord.Interaction) -> bool:
    """Whether the invoking member administrates the guild (live permissions)."""
    permissions = getattr(interaction.user, "guild_permissions", None)
    return bool(permissions and (permissions.administrator or permissions.manage_guild))


async def is_admin(
    interaction: discord.Interaction,
    bot_admins: tuple[str, ...],
    roles_service: RolesService | None = None,
) -> bool:
    """Whether the interaction may act as an admin — validated at click time.

    BOT_ADMINS and guild administrators pass without any lookup; the
    ``bot-admins`` guild role resolves live through the RolesService
    (no stale cache across role renames or grants).
    """
    user_id = getattr(interaction.user, "id", None)
    if is_bot_admin(user_id, bot_admins) or is_guild_admin(interaction):
        return True
    if roles_service is None:
        return False
    try:
        role_id = await roles_service.resolve_admin_role(str(interaction.guild_id) if interaction.guild_id else "")
    except Exception:
        logger.warning("ADMIN ROLE LOOKUP FAILED — denying", exc_info=True)
        return False
    if not role_id or not role_id.isdigit():
        return False
    return int(role_id) in _member_role_ids(interaction)


async def require_admin(
    interaction: discord.Interaction,
    bot_admins: tuple[str, ...],
    roles_service: RolesService | None = None,
) -> bool:
    """Guard an interactive callback: validate at click time, deny ephemerally.

    Returns True when the interaction may proceed; on denial the user
    gets the ephemeral reason and the caller must return immediately.
    """
    if await is_admin(interaction, bot_admins, roles_service):
        return True
    try:
        if interaction.response.is_done():
            await interaction.followup.send(DENIED_MESSAGE, ephemeral=True)
        else:
            await interaction.response.send_message(DENIED_MESSAGE, ephemeral=True)
    except Exception:
        logger.warning("DENIAL ANSWER FAILED — best-effort", exc_info=True)
    return False
