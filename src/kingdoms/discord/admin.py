"""The /admin command: operator panel built as a Components V2 layout.

Access is restricted to bot operators (``BOT_ADMINS``) plus the guild's
administrators (developer decision, kingdoms-services#109): the panel
and its actions are operational, the gate is in place before further
admin features land.

The panel manages the **bot logs channel** (kingdoms-services#109):

- **routing** — pick any guild text channel from a dropdown; the
  current visibility policy (and role grants) is re-applied on the
  new target, so the rights follow the routing;
- **visibility** — admin-only (default) or public, synchronized with
  the Discord permission overwrites and persisted in the policy;
- **language** — fr/en, persisted per guild (guild settings).

Every action is audited as a lifecycle event in the logs channel
itself. All interactive components are built through the UI SDK
(ADR-0009): Actions, SelectMenus and a ChannelSelect (channel picker
with native autocomplete), custom IDs following the
``<mod>:<component>:<payload>`` convention.

Reference: kingdoms-services#102, #109.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands

from kingdoms.core.services.logs import LogService
from kingdoms.discord.ui import (
    BLURPLE,
    Action,
    ChannelSelect,
    Container,
    Option,
    Row,
    SelectMenu,
    Separator,
    Text,
    UILayout,
)

logger = logging.getLogger("kingdoms.admin")

PING_BUTTON_ID = "admin:button:ping"
VISIBILITY_SELECT_ID = "admin:select:visibility"
LOCALE_SELECT_ID = "admin:select:locale"
CHANNEL_SELECT_ID = "admin:channels:logs"

VISIBILITY_ADMIN_ONLY = "admin_only"
VISIBILITY_PUBLIC = "public"
LOCALES = ("en", "fr")


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


def _visibility_lines(channel_id: str | None, policy: dict[str, object], locale: str) -> list[str]:
    """Render the logs policy status lines (channel, visibility, language)."""
    status = f"<#{channel_id}>" if channel_id else "not provisioned yet"
    visibility = str(policy.get("default", "admin_only"))
    visibility_label = "public 🔓" if visibility == VISIBILITY_PUBLIC else "admin-only 🔒"
    return [
        f"Channel: {status}",
        f"Visibility: {visibility_label}",
        f"Language: {locale}",
    ]


async def build_admin_panel(
    logs_service: LogService,
    guild_id: str,
    by: str,
) -> discord.ui.LayoutView:
    """Build the live admin panel for a guild: routing, visibility, language.

    Pure display + interactive components, all through the UI SDK; the
    callbacks close over the guild so the same panel serves every guild.
    """
    channel_id = await logs_service.resolve_channel(guild_id)
    policy = await logs_service.get_access_policy(guild_id)
    locale = await logs_service.get_locale(guild_id)

    async def on_channel(interaction: discord.Interaction, values: list[str]) -> None:
        if not values:
            return
        try:
            await logs_service.set_channel(guild_id, values[0], by=by)
        except Exception:
            logger.exception("ADMIN PANEL: channel routing failed for guild %s", guild_id)
            await interaction.response.send_message("Routing failed — see the bot logs.", ephemeral=True)
            return
        await interaction.response.edit_message(view=await build_admin_panel(logs_service, guild_id, by))

    async def on_visibility(interaction: discord.Interaction, values: list[str]) -> None:
        if not values:
            return
        public = values[0] == VISIBILITY_PUBLIC
        try:
            await logs_service.set_visibility(guild_id, public, by=by)
        except Exception:
            logger.exception("ADMIN PANEL: visibility change failed for guild %s", guild_id)
            await interaction.response.send_message("Visibility change failed — see the bot logs.", ephemeral=True)
            return
        await interaction.response.edit_message(view=await build_admin_panel(logs_service, guild_id, by))

    async def on_locale(interaction: discord.Interaction, values: list[str]) -> None:
        if not values:
            return
        try:
            await logs_service.set_locale(guild_id, values[0], by=by)
        except Exception:
            logger.exception("ADMIN PANEL: locale change failed for guild %s", guild_id)
            await interaction.response.send_message("Language change failed — see the bot logs.", ephemeral=True)
            return
        await interaction.response.edit_message(view=await build_admin_panel(logs_service, guild_id, by))

    channel_select = ChannelSelect(
        custom_id=CHANNEL_SELECT_ID,
        on_choose=on_channel,
        placeholder="Route the bot logs to a channel…",
    )
    visibility_select = SelectMenu(
        custom_id=VISIBILITY_SELECT_ID,
        options=(
            Option("🔒 Admin-only", VISIBILITY_ADMIN_ONLY, "Guild admins and BOT_ADMINS only"),
            Option("🔓 Public", VISIBILITY_PUBLIC, "Everyone may read the logs"),
        ),
        on_choose=on_visibility,
        placeholder="Visibility…",
    )
    locale_select = SelectMenu(
        custom_id=LOCALE_SELECT_ID,
        options=(
            Option("🇬🇧 English", "en", "English messages"),
            Option("🇫🇷 Français", "fr", "Messages en français"),
        ),
        on_choose=on_locale,
        placeholder="Language…",
    )
    ping = Action("🏓 Ping", PING_BUTTON_ID, _noop_ping, style="secondary")
    container = (
        Container(accent=BLURPLE)
        .add(Text("# Kingdoms — Admin"))
        .add(Text("\n".join(_visibility_lines(channel_id, policy, locale))))
        .add(Separator())
        .add(Row(channel_select))
        .add(Row(visibility_select))
        .add(Row(locale_select))
        .add(Separator())
        .add(Text("Operator panel. Ping checks that the bot reacts to clicks."))
        .add(Row(ping))
    )
    return UILayout().add(container).build()


async def _noop_ping(interaction: discord.Interaction) -> None:
    """Answer the SDK ping button with a visible pong."""
    await interaction.response.send_message("pong", ephemeral=True)


def build_logs_policy_view(guild_id: str, channel_id: str | None, policy_lines: list[str]) -> discord.ui.LayoutView:
    """Build the logs policy section: pure display, built through the UI SDK."""
    status = f"<#{channel_id}>" if channel_id else "not provisioned yet"
    container = (
        Container(accent=BLURPLE)
        .add(Text("## 🤖 Bot logs channel"))
        .add(Text(f"Channel: {status}"))
        .add(Text("\n".join(policy_lines) if policy_lines else "Default policy: admin-only."))
    )
    return UILayout().add(container).build()


def build_admin_note_view(message: str) -> discord.ui.LayoutView:
    """Build a single-note admin layout (degradation paths), through the UI SDK."""
    return UILayout().add(Container(accent=BLURPLE).add(Text(message))).build()


def build_admin_layout() -> AdminLayout:
    """Build the /admin layout (standalone for tests)."""
    return AdminLayout()


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
            if role is not None:
                await logs_service.grant_role_view_access(guild_id, str(role.id), by=str(user_id))
            panel = await build_admin_panel(logs_service, guild_id, by=str(user_id))
        except Exception as exc:
            logger.exception("ADMIN PANEL: logs management failed for guild %s", guild_id)
            detail = f"{type(exc).__name__}: {exc}"[:120]
            await interaction.response.send_message(
                view=build_admin_note_view(
                    f"Bot logs management failed — `{discord.utils.escape_markdown(detail)}`"
                ),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(view=panel, ephemeral=True)
