"""The /admin command: a hierarchical operator panel (Components V2).

Access is restricted to bot operators (``BOT_ADMINS``), guild
administrators and the ``bot-admins`` role (kingdoms-services#109,
#115) — validated at invocation **and at click time** (the developer
mandate: never assume that seeing a component means being allowed to
click it).

Structure (kingdoms-services#117):

- **main menu** — the guild's global settings (language fr/en, used
  for every channel message: announcements, lifecycle events, panels)
  and the managed-channel picker (🛰 Bot logs, 🛡 Bot admins);
- **channel menu** — the settings of the selected managed channel:
  routing (which guild channel carries it) and visibility
  (public/admin-only, synchronized with the Discord overwrites);
- **DM setup** — outside a guild, /admin manages the user's personal
  DM locale (error reports, enrollment flows, match reports — every
  DM the bot sends to that user), persisted per user in
  ``user_settings``: DMs follow the user, not a guild.

Every action is audited as a lifecycle event in the logs channel.
All interactive components are built through the UI SDK (ADR-0009):
Actions, SelectMenus and a ChannelSelect, custom IDs following the
``<mod>:<component>:<payload>`` convention.

Reference: kingdoms-services#102, #109, #115, #117.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

import discord
from discord import app_commands

from kingdoms.core.services.admin_channel import ADMIN_CHANNEL_CATEGORY, AdminChannelService
from kingdoms.core.services.i18n import MessageCatalog
from kingdoms.core.services.logs import BOT_LOGS_CATEGORY, LogService
from kingdoms.core.services.roles import RolesService
from kingdoms.discord.commands_i18n import localized
from kingdoms.discord.guards import require_admin
from kingdoms.discord.ui import (
    BLURPLE,
    Container,
    Text,
    UILayout,
)

logger = logging.getLogger("kingdoms.admin")

ErrorReporter = Callable[[discord.Interaction, BaseException], Awaitable[None]]


async def _report_failure(
    reporter: ErrorReporter | None,
    interaction: discord.Interaction,
    exc: BaseException,
) -> None:
    """Forward a callback failure to the crash reporter (#113), best-effort.

    A failure caught by a panel callback must still reach bot-logs
    (cause, context, GitHub source link) and the BOT_ADMINS DMs — the
    ephemeral answer alone hides the actionable details.
    """
    if reporter is None:
        return
    try:
        await reporter(interaction, exc)
    except Exception:
        logger.warning("ADMIN PANEL: error reporting failed — best-effort", exc_info=True)


CHANNEL_MENU_ID = "admin:select:channel"
LOCALE_SELECT_ID = "admin:select:locale"
USER_LOCALE_SELECT_ID = "admin:select:user-locale"
VISIBILITY_SELECT_ID = "admin:select:visibility"
CHANNEL_ROUTE_ID = "admin:channels:logs"
BACK_BUTTON_ID = "admin:button:back"

VISIBILITY_ADMIN_ONLY = "admin_only"
VISIBILITY_PUBLIC = "public"
LOCALES = ("en", "fr")

# (category, icon, i18n label key) — the label renders through the
# guild's locale (admin.channel_bot_logs / admin.channel_bot_admins).
HOME_CHANNEL_CATEGORY = "home"
LIVE_CHANNEL_CATEGORY = "live"

MANAGED_CHANNELS: tuple[tuple[str, str, str], ...] = (
    (BOT_LOGS_CATEGORY, "🛰", "channel_bot_logs"),
    (ADMIN_CHANNEL_CATEGORY, "🛡", "channel_bot_admins"),
    (HOME_CHANNEL_CATEGORY, "🏛", "channel_home"),
    (LIVE_CHANNEL_CATEGORY, "📡", "channel_live"),
)

_LOCALE_LABELS = {"en": "🇬🇧 English", "fr": "🇫🇷 Français"}


def _t(catalog: MessageCatalog | None, locale: str, key: str, **kwargs: object) -> str:
    """Render an admin catalog key with an English fallback."""
    if catalog is None:
        return key
    return catalog.render(f"admin.{key}", locale, **kwargs)


def _is_bot_admin(user_id: int | None, bot_admins: tuple[str, ...]) -> bool:
    """Whether the invoking user is a bot operator (BOT_ADMINS)."""
    if user_id is None:
        return False
    return str(user_id) in bot_admins


def _is_guild_admin(interaction: discord.Interaction) -> bool:
    """Whether the invoking member administrates the guild."""
    permissions = getattr(interaction.user, "guild_permissions", None)
    return bool(permissions and (permissions.administrator or permissions.manage_guild))


async def _managed_channel_status(
    logs_service: LogService,
    guild_id: str,
    admin_channel_service: AdminChannelService | None = None,
) -> dict[str, str]:
    """Render the per-category channel mentions for the main menu."""
    status: dict[str, str] = {}
    for category, _, _ in MANAGED_CHANNELS:
        channel_id = await _resolve_managed_channel(logs_service, guild_id, category, admin_channel_service)
        status[category] = f"<#{channel_id}>" if channel_id else "not provisioned yet"
    return status


async def _resolve_managed_channel(
    logs_service: LogService,
    guild_id: str,
    category: str,
    admin_channel_service: AdminChannelService | None = None,
) -> str | None:
    """Resolve a managed channel through its stored tree id; None when absent.

    The tree is the single owner of the channels: the admin panel
    resolves by the persisted id (``tree:<scope>:<key>``) and never
    creates or adopts — a channel the tree has not provisioned simply
    reads "not provisioned yet".
    """
    del logs_service, admin_channel_service
    from kingdoms.discord.ui.panels_provisioning import stored_channel_id

    tree_keys = {
        BOT_LOGS_CATEGORY: ("bot", "logs"),
        ADMIN_CHANNEL_CATEGORY: ("bot", "admin"),
        HOME_CHANNEL_CATEGORY: ("root", "general"),
        LIVE_CHANNEL_CATEGORY: ("games/aoe2", "live"),
    }
    scope, key = tree_keys.get(category, ("", ""))
    if not scope:
        return None
    try:
        return await stored_channel_id(guild_id, scope, key)
    except Exception:
        logger.warning("ADMIN PANEL: channel resolution failed (guild %s, category %s)", guild_id, category)
        return None


def _panel_client() -> discord.Client | None:
    """Resolve the running bot (the admin module's client reference; None unwired)."""
    from kingdoms.discord.admin_panel_dynamic import _CLIENT_REF

    return _CLIENT_REF[0] if _CLIENT_REF else None


def build_admin_note_view(message: str) -> discord.ui.LayoutView:
    """Build a single-note admin layout (degradation paths), through the UI SDK."""
    return UILayout().add(Container(accent=BLURPLE).add(Text(message))).build()


def register_admin_command(
    tree: app_commands.CommandTree[discord.Client],
    bot_admins: tuple[str, ...] = (),
    logs_service: LogService | None = None,
    roles_service: RolesService | None = None,
    catalog: MessageCatalog | None = None,
    admin_channel_service: AdminChannelService | None = None,
    error_reporter: ErrorReporter | None = None,
) -> None:
    """Register the /admin slash command on the command tree.

    ``bot_admins`` is the parsed BOT_ADMINS operator ids (StatusService).
    ``logs_service`` is the core LogService (kingdoms-services#109); it
    may be None in local runs — the panel degrades to a status note.
    ``roles_service`` resolves the guild's bot-admins role
    (kingdoms-services#115) — members holding it administer too.
    ``catalog`` localizes the panel with the guild's (or user's) locale.
    ``admin_channel_service`` routes the admin channel like the logs
    channel (the same menu governs both managed channels).
    ``error_reporter`` forwards callback failures to the crash reporter
    (#113): bot-logs gets cause, context and the GitHub source link,
    the BOT_ADMINS get the DM — on top of the ephemeral answer.
    Access is validated at invocation time and at click time (guards):
    BOT_ADMINS, guild administrators or the bot-admins role.
    """
    admins = bot_admins

    @tree.command(
        name=localized("commands.admin_name", "admin"),
        description=localized("commands.admin_description", "Admin panel (bot operators and guild admins only)"),
    )
    @app_commands.default_permissions(administrator=True)
    async def admin_command(interaction: discord.Interaction) -> None:
        """Answer the /admin interaction with the right panel.

        The panel reads the guild settings before rendering; deferring
        first keeps the answer inside Discord's interaction window even
        when the reads are slow (a late send_message raises 10062).
        """
        user_id = getattr(interaction.user, "id", None)
        guild_id = str(interaction.guild_id) if interaction.guild_id is not None else ""

        if not guild_id:
            if logs_service is None:
                await interaction.response.send_message(
                    view=build_admin_note_view(_t(catalog, "en", "no_service")),
                    ephemeral=True,
                )
                return
            await interaction.response.defer(ephemeral=True)
            is_bot_admin = _is_bot_admin(user_id, admins)
            if is_bot_admin:
                from kingdoms.discord.admin_dm_panel import build_admin_dm_panel

                # The DM panel only: the language follows the user's
                # Discord client (no platform-stored locale anymore).
                admin_view = await build_admin_dm_panel(interaction)
                await interaction.followup.send(view=admin_view, ephemeral=True)
            else:
                await interaction.followup.send(
                    "Réglages personnels : la langue suit celle de ton client Discord.",
                    ephemeral=True,
                )
            return

        if not (_is_bot_admin(user_id, admins) or _is_guild_admin(interaction)):
            if not await guards_require(interaction, admins, roles_service):
                return

        if logs_service is None:
            await interaction.response.send_message(
                view=build_admin_note_view(_t(catalog, "en", "no_service")),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        try:
            from kingdoms.discord.admin_panel_dynamic import build_pin_main_menu

            locale = await logs_service.get_locale(guild_id)
            panel = await build_pin_main_menu(
                logs_service,
                guild_id,
                catalog,
                locale,
                admin_channel_service,
            )
        except Exception as exc:
            logger.exception("ADMIN PANEL: logs management failed for guild %s", guild_id)
            await _report_failure(error_reporter, interaction, exc)
            detail = f"{type(exc).__name__}: {exc}"[:120]
            await interaction.followup.send(
                view=build_admin_note_view(
                    _t(catalog, "en", "panel_failed", detail=discord.utils.escape_markdown(detail))
                ),
                ephemeral=True,
            )
            return

        await interaction.followup.send(view=panel, ephemeral=True)


async def guards_require(
    interaction: discord.Interaction,
    admins: tuple[str, ...],
    roles_service: RolesService | None,
) -> bool:
    """Guard the invocation; answer the denial ephemerally when refused."""
    if await require_admin(interaction, admins, roles_service):
        return True
    logger.info("admin access denied: user=%s", getattr(interaction.user, "id", None))
    try:
        if interaction.response.is_done():
            await interaction.followup.send(
                "Tu n'es ni opérateur du bot (BOT_ADMINS) ni administrateur du serveur.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "Tu n'es ni opérateur du bot (BOT_ADMINS) ni administrateur du serveur.", ephemeral=True
            )
    except Exception:
        logger.warning("DENIAL ANSWER FAILED — best-effort", exc_info=True)
    return False
