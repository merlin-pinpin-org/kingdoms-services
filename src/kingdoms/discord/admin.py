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
from typing import Any

import discord
from discord import app_commands

from kingdoms.core.services.admin_channel import ADMIN_CHANNEL_CATEGORY, AdminChannelService
from kingdoms.core.services.i18n import MessageCatalog
from kingdoms.core.services.logs import BOT_LOGS_CATEGORY, LogService
from kingdoms.core.services.roles import RolesService
from kingdoms.discord.guards import require_admin
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

CHANNEL_MENU_ID = "admin:select:channel"
LOCALE_SELECT_ID = "admin:select:locale"
USER_LOCALE_SELECT_ID = "admin:select:user-locale"
VISIBILITY_SELECT_ID = "admin:select:visibility"
CHANNEL_ROUTE_ID = "admin:channels:logs"
BACK_BUTTON_ID = "admin:button:back"

VISIBILITY_ADMIN_ONLY = "admin_only"
VISIBILITY_PUBLIC = "public"
LOCALES = ("en", "fr")

MANAGED_CHANNELS: tuple[tuple[str, str, str], ...] = (
    (BOT_LOGS_CATEGORY, "🛰", "Bot logs"),
    (ADMIN_CHANNEL_CATEGORY, "🛡", "Bot admins"),
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


async def build_dm_setup_view(
    logs_service: LogService | None,
    user_id: str,
    bot_admins: tuple[str, ...] = (),
    catalog: MessageCatalog | None = None,
) -> discord.ui.LayoutView:
    """Build the DM /admin panel: the user's personal DM locale."""
    locale = await logs_service.get_user_locale(user_id) if logs_service is not None else "en"

    async def on_user_locale(interaction: discord.Interaction, values: list[str]) -> None:
        """Apply the user's own DM locale choice (self-service only)."""
        if not values or logs_service is None:
            return
        if str(getattr(interaction.user, "id", "")) != user_id:
            await interaction.response.send_message(
                "You are not allowed to change another user's language.", ephemeral=True
            )
            return
        try:
            await logs_service.set_user_locale(str(interaction.user.id), values[0])
        except Exception:
            logger.exception("ADMIN DM: user locale change failed for user %s", interaction.user.id)
            await interaction.response.send_message(
                _t(catalog, locale, "dm_change_failed"), ephemeral=True
            )
            return
        await interaction.response.edit_message(
            view=await build_dm_setup_view(logs_service, str(interaction.user.id), bot_admins, catalog)
        )

    locale_select = SelectMenu(
        custom_id=USER_LOCALE_SELECT_ID,
        options=tuple(Option(_LOCALE_LABELS[loc], loc) for loc in LOCALES),
        on_choose=on_user_locale,
        placeholder=_t(catalog, locale, "dm_placeholder"),
    )
    container = (
        Container(accent=BLURPLE)
        .add(Text(f"# ⚙️ {_t(catalog, locale, 'dm_title')}"))
        .add(Text(f"{_t(catalog, locale, 'dm_language')}: {_LOCALE_LABELS.get(locale, locale)}"))
        .add(Separator())
        .add(Text(_t(catalog, locale, "dm_language_hint")))
        .add(Row(locale_select))
    )
    return UILayout().add(container).build()


async def build_main_menu(
    logs_service: LogService,
    guild_id: str,
    by: str,
    bot_admins: tuple[str, ...] = (),
    roles_service: RolesService | None = None,
    catalog: MessageCatalog | None = None,
    admin_channel_service: AdminChannelService | None = None,
) -> discord.ui.LayoutView:
    """Build the /admin main menu: guild language + managed channels."""
    locale = await logs_service.get_locale(guild_id)
    channel_status = await _managed_channel_status(logs_service, guild_id, admin_channel_service)

    async def on_locale(interaction: discord.Interaction, values: list[str]) -> None:
        """Apply the guild's language choice, then re-render the main menu."""
        if not values:
            return
        if not await require_admin(interaction, bot_admins, roles_service):
            return
        try:
            await logs_service.set_locale(guild_id, values[0], by=by)
        except Exception:
            logger.exception("ADMIN PANEL: locale change failed for guild %s", guild_id)
            await interaction.response.send_message(
                _t(catalog, locale, "dm_change_failed"), ephemeral=True
            )
            return
        await interaction.response.edit_message(
            view=await build_main_menu(logs_service, guild_id, by, bot_admins, roles_service, catalog)
        )

    async def on_channel(interaction: discord.Interaction, values: list[str]) -> None:
        """Open the secondary menu of the selected managed channel."""
        if not values:
            return
        if not await require_admin(interaction, bot_admins, roles_service):
            return
        await interaction.response.edit_message(
            view=await build_channel_menu(
                logs_service,
                guild_id,
                values[0],
                by,
                bot_admins,
                roles_service,
                catalog,
                locale,
                admin_channel_service,
            )
        )

    locale_select = SelectMenu(
        custom_id=LOCALE_SELECT_ID,
        options=tuple(Option(_LOCALE_LABELS[loc], loc) for loc in LOCALES),
        on_choose=on_locale,
        placeholder=_t(catalog, locale, "language_placeholder", value=_LOCALE_LABELS.get(locale, locale)),
    )
    channel_options = tuple(
        Option(
            f"{icon} {label}",
            category,
            channel_status.get(category, _t(catalog, locale, "not_provisioned")),
        )
        for category, icon, label in MANAGED_CHANNELS
    )
    channel_menu = SelectMenu(
        custom_id=CHANNEL_MENU_ID,
        options=channel_options,
        on_choose=on_channel,
        placeholder=_t(catalog, locale, "channels_placeholder"),
    )
    container = (
        Container(accent=BLURPLE)
        .add(Text(f"# ⚙️ {_t(catalog, locale, 'title')}"))
        .add(Separator())
        .add(Text(f"## 🌍 {_t(catalog, locale, 'language')}"))
        .add(
            Text(
                _t(
                    catalog,
                    locale,
                    "language_hint",
                    value=_LOCALE_LABELS.get(locale, locale),
                )
            )
        )
        .add(Row(locale_select))
        .add(Separator())
        .add(Text(f"## 📋 {_t(catalog, locale, 'channels')}"))
        .add(Row(channel_menu))
    )
    return UILayout().add(container).build()


async def build_channel_menu(
    logs_service: LogService,
    guild_id: str,
    category: str,
    by: str,
    bot_admins: tuple[str, ...] = (),
    roles_service: RolesService | None = None,
    catalog: MessageCatalog | None = None,
    locale: str = "en",
    admin_channel_service: AdminChannelService | None = None,
) -> discord.ui.LayoutView:
    """Build the secondary menu of one managed channel (routing, visibility)."""
    entry = next((e for e in MANAGED_CHANNELS if e[0] == category), None)
    if entry is None:
        return UILayout().add(
            Container(accent=BLURPLE).add(Text(f"Unknown channel category: `{category}`."))
        ).build()
    _, icon, label = entry
    channel_id = await _resolve_managed_channel(logs_service, guild_id, category, admin_channel_service)
    policy = await logs_service.get_access_policy(guild_id) if category == BOT_LOGS_CATEGORY else None

    async def on_back(interaction: discord.Interaction) -> None:
        """Return from the secondary menu to the main menu."""
        if not await require_admin(interaction, bot_admins, roles_service):
            return
        await interaction.response.edit_message(
            view=await build_main_menu(logs_service, guild_id, by, bot_admins, roles_service, catalog)
        )

    async def rerender(interaction: discord.Interaction) -> None:
        """Re-render the secondary menu after a routing/visibility change."""
        await interaction.response.edit_message(
            view=await build_channel_menu(
                logs_service,
                guild_id,
                category,
                by,
                bot_admins,
                roles_service,
                catalog,
                locale,
                admin_channel_service,
            )
        )

    router = _category_router(category, logs_service, admin_channel_service)
    on_route = _routing_callback(router, guild_id, by, bot_admins, roles_service, rerender, catalog, locale)
    on_visibility = _visibility_callback(
        logs_service, guild_id, by, bot_admins, roles_service, rerender, catalog, locale
    )

    status = f"<#{channel_id}>" if channel_id else _t(catalog, locale, "not_provisioned")
    blocks: list[Any] = [
        Text(f"# {icon} Kingdoms — {label}"),
        Separator(),
        Text(f"{_t(catalog, locale, 'channel')}: {status}"),
    ]
    if category == BOT_LOGS_CATEGORY:
        blocks.extend(
            _logs_channel_blocks(
                logs_service, guild_id, category, by, policy, on_route, on_visibility, catalog, locale
            )
        )
    else:
        blocks.extend(
            [
                Separator(),
                Text(_t(catalog, locale, "admin_channel_note")),
                Row(
                    ChannelSelect(
                        custom_id=CHANNEL_ROUTE_ID,
                        on_choose=on_route,
                        placeholder=_t(catalog, locale, "route_placeholder"),
                    )
                ),
            ]
        )

    blocks.extend(
        [Separator(), Row(Action(_t(catalog, locale, "back"), BACK_BUTTON_ID, on_back, style="secondary"))]
    )
    return UILayout().add(Container(accent=BLURPLE, blocks=tuple(blocks))).build()


def _category_router(
    category: str,
    logs_service: LogService,
    admin_channel_service: AdminChannelService | None,
) -> Any:
    """Resolve the routing callable of a managed channel category."""

    async def route_logs(guild_id: str, channel_id: str, by: str) -> None:
        """Route the bot logs channel (LogService.set_channel)."""
        await logs_service.set_channel(guild_id, channel_id, by=by)

    async def route_admin(guild_id: str, channel_id: str, by: str) -> None:
        """Route the admin channel (AdminChannelService.set_channel)."""
        if admin_channel_service is None:
            raise RuntimeError("admin channel management is unavailable (no AdminChannelService wired)")
        await admin_channel_service.set_channel(guild_id, channel_id)

    return route_admin if category == ADMIN_CHANNEL_CATEGORY else route_logs


def _routing_callback(
    router: Any,
    guild_id: str,
    by: str,
    bot_admins: tuple[str, ...],
    roles_service: RolesService | None,
    rerender: Any,
    catalog: MessageCatalog | None = None,
    locale: str = "en",
) -> Any:
    """Build the routing select callback: guard, route, rerender."""

    async def on_route(interaction: discord.Interaction, values: list[str]) -> None:
        """Apply the channel routing picked in the ChannelSelect."""
        if not values:
            return
        if not await require_admin(interaction, bot_admins, roles_service):
            return
        try:
            await router(guild_id, values[0], by)
        except Exception:
            logger.exception("ADMIN PANEL: channel routing failed for guild %s", guild_id)
            await interaction.response.send_message(_t(catalog, locale, "route_failed"), ephemeral=True)
            return
        await rerender(interaction)

    return on_route


def _visibility_callback(
    logs_service: LogService,
    guild_id: str,
    by: str,
    bot_admins: tuple[str, ...],
    roles_service: RolesService | None,
    rerender: Any,
    catalog: MessageCatalog | None = None,
    locale: str = "en",
) -> Any:
    """Build the visibility select callback: guard, persist, rerender."""

    async def on_visibility(interaction: discord.Interaction, values: list[str]) -> None:
        """Apply the visibility picked in the select (public/admin-only)."""
        if not values:
            return
        if not await require_admin(interaction, bot_admins, roles_service):
            return
        try:
            await logs_service.set_visibility(guild_id, values[0] == VISIBILITY_PUBLIC, by=by)
        except Exception:
            logger.exception("ADMIN PANEL: visibility change failed for guild %s", guild_id)
            await interaction.response.send_message(_t(catalog, locale, "visibility_failed"), ephemeral=True)
            return
        await rerender(interaction)

    return on_visibility


def _logs_channel_blocks(
    logs_service: LogService,
    guild_id: str,
    category: str,
    by: str,
    policy: dict[str, object] | None,
    on_route: Any,
    on_visibility: Any,
    catalog: MessageCatalog | None = None,
    locale: str = "en",
) -> list[Any]:
    """Build the logs-channel specific blocks: status + routing + visibility."""
    visibility = str((policy or {}).get("default", VISIBILITY_ADMIN_ONLY))
    visibility_label = (
        _t(catalog, locale, "visibility_public")
        if visibility == VISIBILITY_PUBLIC
        else _t(catalog, locale, "visibility_admin_only")
    )
    route_select = ChannelSelect(
        custom_id=CHANNEL_ROUTE_ID,
        on_choose=on_route,
        placeholder=_t(catalog, locale, "route_placeholder"),
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
    return [
        Text(f"{_t(catalog, locale, 'visibility')}: {visibility_label}"),
        Separator(),
        Row(route_select),
        Row(visibility_select),
    ]


async def _managed_channel_status(
    logs_service: LogService,
    guild_id: str,
    admin_channel_service: AdminChannelService | None = None,
) -> dict[str, str]:
    """Render the per-category channel mentions for the main menu."""
    status: dict[str, str] = {}
    for category, _, _ in MANAGED_CHANNELS:
        channel_id = await _resolve_managed_channel(
            logs_service, guild_id, category, admin_channel_service
        )
        status[category] = f"<#{channel_id}>" if channel_id else "not provisioned yet"
    return status


async def _resolve_managed_channel(
    logs_service: LogService,
    guild_id: str,
    category: str,
    admin_channel_service: AdminChannelService | None = None,
) -> str | None:
    """Resolve a managed channel id, degrading to None on failure."""
    try:
        if category == BOT_LOGS_CATEGORY:
            return await logs_service.resolve_channel(guild_id)
        if category == ADMIN_CHANNEL_CATEGORY and admin_channel_service is not None:
            return await admin_channel_service.resolve_channel(guild_id)
    except Exception:
        logger.warning("ADMIN PANEL: channel resolution failed (guild %s, category %s)", guild_id, category)
    return None


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
    Access is validated at invocation time and at click time (guards):
    BOT_ADMINS, guild administrators or the bot-admins role.
    """
    admins = bot_admins

    @tree.command(name="admin", description="Admin panel (bot operators and guild admins only)")
    @app_commands.default_permissions(administrator=True)
    async def admin_command(interaction: discord.Interaction) -> None:
        """Answer the /admin interaction with the right panel."""
        user_id = getattr(interaction.user, "id", None)
        guild_id = str(interaction.guild_id) if interaction.guild_id is not None else ""

        if not guild_id:
            if logs_service is None:
                await interaction.response.send_message(
                    view=build_admin_note_view(_t(catalog, "en", "no_service")),
                    ephemeral=True,
                )
                return
            await interaction.response.send_message(
                view=await build_dm_setup_view(logs_service, str(user_id), admins, catalog), ephemeral=True
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

        try:
            panel = await build_main_menu(
                logs_service,
                guild_id,
                str(user_id),
                admins,
                roles_service,
                catalog,
                admin_channel_service,
            )
        except Exception as exc:
            logger.exception("ADMIN PANEL: logs management failed for guild %s", guild_id)
            detail = f"{type(exc).__name__}: {exc}"[:120]
            await interaction.response.send_message(
                view=build_admin_note_view(
                    _t(catalog, "en", "panel_failed", detail=discord.utils.escape_markdown(detail))
                ),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(view=panel, ephemeral=True)


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
                "You are not a bot operator (BOT_ADMINS) nor a guild administrator.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "You are not a bot operator (BOT_ADMINS) nor a guild administrator.", ephemeral=True
            )
    except Exception:
        logger.warning("DENIAL ANSWER FAILED — best-effort", exc_info=True)
    return False
