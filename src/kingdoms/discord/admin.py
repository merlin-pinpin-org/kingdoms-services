"""The /admin command: operator panel built as a Components V2 layout.

Access is restricted to bot operators (``BOT_ADMINS``) plus the guild's
administrators (developer decision, kingdoms-services#109): the panel
and its actions are operational, the gate is in place before further
admin features land.

The panel exposes the **bot logs channel management** (kingdoms-services
#109): the resolved 🤖-bot-logs channel for the invoking guild, its
access policy, and the policy action — grant a role view access through
the command's ``role`` parameter. Every action is audited as a lifecycle
event in the logs channel itself.

First command using a ``LayoutView`` (ADR-0009 dual Discord UI system):
instead of a plain embed, the answer is a Components V2 layout — a
container with a text header and a section whose button reacts live.
Custom IDs follow the ``<mod>:<component>:<payload>`` convention.

Reference: kingdoms-services#102, #109.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands

from kingdoms.core.services.logs import LogService

logger = logging.getLogger("kingdoms.admin")

PING_BUTTON_ID = "admin:button:ping"


def _is_bot_admin(user_id: int | None, bot_admins: tuple[str, ...]) -> bool:
    """Whether the invoking user is a bot operator (BOT_ADMINS)."""
    if user_id is None:
        return False
    return str(user_id) in bot_admins


def _is_guild_admin(interaction: discord.Interaction) -> bool:
    """Whether the invoking member administrates the guild."""
    permissions = getattr(interaction.user, "guild_permissions", None)
    return bool(permissions and (permissions.administrator or permissions.manage_guild))


class AdminLayout(discord.ui.LayoutView):
    """The /admin answer: a Components V2 layout with the operator actions."""

    def __init__(self, bot_admins: tuple[str, ...] = (), logs_service: LogService | None = None) -> None:
        super().__init__(timeout=300)
        self.bot_admins = bot_admins
        self.logs_service = logs_service
        button: discord.ui.Button[AdminLayout] = discord.ui.Button(
            label="Ping",
            style=discord.ButtonStyle.primary,
            custom_id=PING_BUTTON_ID,
        )
        button.callback = self.on_ping  # type: ignore[method-assign]
        container = discord.ui.Container(
            discord.ui.TextDisplay("# Kingdoms — Admin"),
            discord.ui.Section(
                discord.ui.TextDisplay("Operator panel. Ping checks that the bot reacts to clicks."),
                accessory=button,
            ),
        )
        self.add_item(container)

    async def on_ping(self, interaction: discord.Interaction) -> None:
        """Answer the ping button click with a visible pong."""
        await interaction.response.send_message("pong", ephemeral=True)


class LogsPolicyLayout(discord.ui.LayoutView):
    """The bot logs channel management section: policy status and actions."""

    def __init__(self, guild_id: str, channel_id: str | None, policy_lines: list[str]) -> None:
        super().__init__(timeout=300)
        status = f"<#{channel_id}>" if channel_id else "not provisioned yet"
        container: discord.ui.Container[LogsPolicyLayout] = discord.ui.Container(
            discord.ui.TextDisplay("## 🤖 Bot logs channel"),
            discord.ui.TextDisplay(f"Channel: {status}"),
            discord.ui.TextDisplay("\n".join(policy_lines) if policy_lines else "Default policy: admin-only."),
        )
        self.add_item(container)


def build_admin_layout() -> AdminLayout:
    """Build the /admin layout (standalone for tests)."""
    return AdminLayout()


def build_logs_policy_layout(guild_id: str, channel_id: str | None, policy_lines: list[str]) -> LogsPolicyLayout:
    """Build the logs policy section (standalone for tests)."""
    return LogsPolicyLayout(guild_id, channel_id, policy_lines)


def register_admin_command(
    tree: app_commands.CommandTree[discord.Client],
    bot_admins: tuple[str, ...] = (),
    logs_service: LogService | None = None,
) -> None:
    """Register the /admin slash command on the command tree.

    ``bot_admins`` is the parsed BOT_ADMINS operator ids (StatusService).
    ``logs_service`` is the core LogService (kingdoms-services#109); it
    may be None in local runs — the logs section degrades to a status
    note. Access: BOT_ADMINS or guild administrators (ephemeral panel).
    """
    admins = bot_admins

    @tree.command(name="admin", description="Admin panel (bot operators and guild admins only)")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(role="Grant a role view access to the bot logs channel")
    async def admin_command(interaction: discord.Interaction, role: discord.Role | None = None) -> None:
        """Answer the /admin interaction with the layout view."""
        user_id = getattr(interaction.user, "id", None)
        if not (_is_bot_admin(user_id, admins) or _is_guild_admin(interaction)):
            logger.info(
                "admin access denied: user=%s is neither BOT_ADMINS nor a guild admin",
                user_id,
            )
            await interaction.response.send_message(
                "You are not a bot operator (BOT_ADMINS) nor a guild administrator.",
                ephemeral=True,
            )
            return

        if logs_service is None:
            layout = AdminLayout(admins)
            layout.add_item(
                discord.ui.Container(
                    discord.ui.TextDisplay("Bot logs management is unavailable (no LogService wired)."),
                )
            )
            await interaction.response.send_message(view=layout, ephemeral=True)
            return

        guild_id = str(interaction.guild_id) if interaction.guild_id is not None else ""
        if not guild_id:
            await interaction.response.send_message(view=AdminLayout(admins), ephemeral=True)
            return

        try:
            channel_id = await logs_service.resolve_channel(guild_id)
            if role is not None:
                await logs_service.grant_role_view_access(guild_id, str(role.id), by=str(user_id))
            policy = await logs_service.get_access_policy(guild_id)
        except Exception:
            logger.exception("ADMIN PANEL: logs management failed for guild %s", guild_id)
            failure = AdminLayout(admins)
            failure.add_item(
                discord.ui.Container(
                    discord.ui.TextDisplay("Bot logs management failed — see the bot logs."),
                )
            )
            await interaction.response.send_message(view=failure, ephemeral=True)
            return

        policy_lines = [
            f"Default policy: `{policy['default']}`",
            f"Roles with view: {', '.join(f'<@&{r}>' for r in policy['roles_with_view']) or 'none'}",
        ]
        if role is not None:
            policy_lines.append(f"Granted view to <@&{role.id}> — the change is audited in the logs channel.")

        await interaction.response.send_message(
            view=build_logs_policy_layout(guild_id, channel_id, policy_lines),
            ephemeral=True,
        )
