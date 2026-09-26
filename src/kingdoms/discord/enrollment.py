"""The enrollment workflow screen (kingdoms-services#115).

The first admin-message with actions: the enrollment panel posts a
workflow screen (steps + operator actions) in the guild, and every
action is validated at click time through the runtime guards
(:mod:`kingdoms.discord.guards`) — **seeing a button never implies
being allowed to click it**.

Today the screen is a skeleton: no mod, no game — the steps that
exist in the design but not in the code render as disabled buttons
(the designer sees where the workflow goes), and the live actions
are Open/Close enrollment plus the ``bot-admins`` role provisioning.
Every string renders through the shared MessageCatalog (enrollment.*
keys) in the guild's locale.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands

from kingdoms.core.services.admin_channel import AdminChannelService
from kingdoms.core.services.i18n import MessageCatalog
from kingdoms.core.services.logs import LogService
from kingdoms.core.services.roles import RolesService
from kingdoms.discord.guards import require_admin
from kingdoms.discord.ui import Action
from kingdoms.discord.ui.factory import Handler
from kingdoms.discord.ui.screens import build_enrollment_screen

logger = logging.getLogger("kingdoms.enrollment")

OPEN_ID = "enrollment:toggle:open"
CLOSE_ID = "enrollment:toggle:close"
ROLE_ID = "enrollment:admins:role"

_FALLBACK_CATALOG: MessageCatalog | None = None


def _fallback_catalog() -> MessageCatalog:
    """Lazy shared catalog for the no-catalog callers (tests, local tools)."""
    global _FALLBACK_CATALOG
    if _FALLBACK_CATALOG is None:
        _FALLBACK_CATALOG = MessageCatalog("config")
    return _FALLBACK_CATALOG


def _tr(catalog: MessageCatalog | None, locale: str, key: str, **kwargs: object) -> str:
    """Render an enrollment.* key through the shared catalog.

    The wired runtime always passes the guild's catalog; without one a
    lazy catalog stands (the yaml still localizes — no raw keys).
    """
    return (catalog or _fallback_catalog()).render(f"enrollment.{key}", locale, **kwargs)


async def _answer(interaction: discord.Interaction, message: str) -> None:
    """Answer an interaction ephemerally (follow-up aware)."""
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


def build_enrollment_actions(
    *,
    on_open: Handler,
    on_close: Handler,
    on_role: Handler,
    catalog: MessageCatalog | None = None,
    locale: str = "en",
) -> tuple[list[Action], list[Action]]:
    """Build the enrollment action rows: live admin actions + disabled future steps."""
    admin_actions = [
        Action(f"🔓 {_tr(catalog, locale, 'open')}", OPEN_ID, on_open, style="success"),
        Action(f"🔒 {_tr(catalog, locale, 'close')}", CLOSE_ID, on_close, style="danger"),
        Action(f"🛡️ {_tr(catalog, locale, 'role')}", ROLE_ID, on_role, style="secondary"),
    ]
    disabled_actions = [
        Action(
            f"🎮 {_tr(catalog, locale, 'select_game')}",
            "enrollment:game:pick",
            _noop,
            style="secondary",
            disabled=True,
        ),
        Action(
            f"🧩 {_tr(catalog, locale, 'configure_mod')}",
            "enrollment:mod:pick",
            _noop,
            style="secondary",
            disabled=True,
        ),
    ]
    return admin_actions, disabled_actions


async def _noop(interaction: discord.Interaction) -> None:
    """Never called: the future-step buttons render disabled."""


def build_enrollment_view(
    bot_admins: tuple[str, ...],
    roles_service: RolesService | None,
    catalog: MessageCatalog | None = None,
    locale: str = "en",
) -> discord.ui.LayoutView:
    """Build the enrollment screen with runtime-guarded admin actions."""
    state = {"open": False}

    async def guarded(interaction: discord.Interaction) -> None:
        """Guard then toggle: click-time validation, never visibility-trust."""
        if not await require_admin(
            interaction,
            bot_admins,
            roles_service,
            denied_message=(catalog or _fallback_catalog()).render("guards.denied", locale),
        ):
            return
        state["open"] = not state["open"]
        action = "opened" if state["open"] else "closed"
        logger.info("ENROLLMENT %s by %s", action, interaction.user.id)
        await _answer(interaction, _tr(catalog, locale, action))

    async def provision(interaction: discord.Interaction) -> None:
        """Guard then ensure the bot-admins role exists in the guild."""
        if not await require_admin(
            interaction,
            bot_admins,
            roles_service,
            denied_message=(catalog or _fallback_catalog()).render("guards.denied", locale),
        ):
            return
        if roles_service is None or interaction.guild_id is None:
            await _answer(interaction, _tr(catalog, locale, "role_unavailable"))
            return
        role_id = await roles_service.provision_admin_role(str(interaction.guild_id))
        await _answer(interaction, _tr(catalog, locale, "role_ready", role_id=role_id))

    on_open, on_close, on_role = guarded, guarded, provision
    admin_actions, disabled_actions = build_enrollment_actions(
        on_open=on_open, on_close=on_close, on_role=on_role, catalog=catalog, locale=locale
    )
    steps = [
        (_tr(catalog, locale, "step_open"), _tr(catalog, locale, "step_open_hint"), False),
        (_tr(catalog, locale, "step_game"), _tr(catalog, locale, "step_game_hint"), False),
        (_tr(catalog, locale, "step_mod"), _tr(catalog, locale, "step_mod_hint"), False),
    ]
    return build_enrollment_screen(
        _tr(catalog, locale, "title"),
        steps,
        mod="enrollment",
        admin_actions=admin_actions,
        disabled_actions=disabled_actions,
    )


async def _locale(logs_service: LogService | None, interaction: discord.Interaction) -> str:
    """Resolve the invoking guild's locale; en when unknown."""
    if logs_service is None or interaction.guild_id is None:
        return "en"
    try:
        return await logs_service.get_locale(str(interaction.guild_id))
    except Exception:
        logger.warning("guild locale lookup failed (guild %s) — falling back", interaction.guild_id)
        return "en"


def register_enrollment_command(
    tree: app_commands.CommandTree[discord.Client],
    bot_admins: tuple[str, ...] = (),
    roles_service: RolesService | None = None,
    admin_channel_service: AdminChannelService | None = None,
    logs_service: LogService | None = None,
    catalog: MessageCatalog | None = None,
) -> None:
    """Register the /enrollment slash command on the command tree.

    The command posts the enrollment screen in the guild's 🛡-bot-admins
    channel — the home of every admin message with actions
    (transparency rule, kingdoms-services#115). Admin-only too: the
    guard runs at invocation time (the default_permissions hint only
    hides the entry, it never replaces the runtime check). Without the
    AdminChannelService the screen degrades to the invoking context.
    """

    @tree.command(name="enrollment", description="Post the enrollment workflow screen (admins only)")
    @app_commands.default_permissions(administrator=True)
    async def enrollment_command(interaction: discord.Interaction) -> None:
        """Post the enrollment screen in the admin channel (admins only)."""
        if not await require_admin(
            interaction,
            bot_admins,
            roles_service,
            denied_message=(catalog or _fallback_catalog()).render(
                "guards.denied", await _locale(logs_service, interaction)
            ),
        ):
            return
        locale = await _locale(logs_service, interaction)
        view = build_enrollment_view(bot_admins, roles_service, catalog, locale)
        guild_id = str(interaction.guild_id) if interaction.guild_id is not None else ""
        posted = False
        if admin_channel_service is not None and guild_id:
            message_id = await admin_channel_service.deliver(guild_id, view, admin_ids=bot_admins)
            posted = message_id is not None
        if posted:
            await _answer(interaction, _tr(catalog, locale, "posted"))
            return
        if interaction.response.is_done():
            await interaction.followup.send(view=view)
        else:
            await interaction.response.send_message(view=view)
