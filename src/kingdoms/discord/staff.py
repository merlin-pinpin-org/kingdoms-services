"""The staff surface: context menu application + admin decision buttons.

A member applies through the **context menu** (right-click on a user →
"Kingdoms: Staff") or a home button; the admins get a notice in the
admin channel with persistent **Accept/Decline** buttons — the decision
syncs the mod's staff role through the platform wiring.

Namespace uniqueness (the §3b rule): the decisions ride the
``staff:decide:`` namespace, served only by the DynamicItems declared
here — restart-proof, exactly one dispatch path.

- ``staff:apply:<mod>:<user_id>``  — the application (menu/button payload);
- ``staff:decide:<accept|decline>:<mod>:<user_id>`` — the decision buttons.
"""
from __future__ import annotations

import logging
import re
from typing import Any

import discord
from discord import app_commands

from kingdoms.core.services.season_roles import SeasonRolesService
from kingdoms.core.services.staff import StaffService

logger = logging.getLogger("kingdoms.staff")

STAFF_APPLY_MARKER = "staff:apply:"
STAFF_DECIDE_MARKER = "staff:decide:"
STAFF_DEFAULT_MOD = "ladder"


class StaffApplyButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=STAFF_APPLY_MARKER + r"(?P<mod>[a-z0-9_-]+)",
):
    """A persistent 'apply for staff' button (state rides the custom_id)."""

    def __init__(self, mod: str, label: str = "Candidater staff") -> None:
        super().__init__(
            discord.ui.Button(
                custom_id=f"{STAFF_APPLY_MARKER}{mod}",
                label=label,
                emoji="🛡️",
                style=discord.ButtonStyle.primary,
            )
        )
        self.mod = mod

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> StaffApplyButton:
        """Rebuild the button from the wire at click time."""
        del interaction, item
        return cls(match.group("mod"))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the application modal (confirmation + motivation)."""
        await interaction.response.send_modal(StaffApplyModal(self.mod))


class StaffApplyModal(discord.ui.Modal):
    """The application form: confirmation + free-text motivation.

    The applicant confirms their candidacy and writes the message the
    admins will read on the notice — the platform never asks for a
    command, every input is a view interaction.
    """

    def __init__(self, mod: str) -> None:
        super().__init__(title=f"Candidature staff {mod}", timeout=None)
        self.mod = mod
        self.message: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label="Ton message de candidature",
            placeholder="Pourquoi toi ? Dispo, experience, envies...",
            style=discord.TextStyle.paragraph,
            max_length=1000,
            required=True,
        )
        self.add_item(self.message)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Record the application with its message, then confirm."""
        await _apply(interaction, self.mod, message=str(self.message.value or "").strip())


class StaffDecideButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=STAFF_DECIDE_MARKER + r"(?P<decision>accept|decline):(?P<mod>[a-z0-9_-]+):(?P<user_id>[0-9]+)",
):
    """A persistent decision button carried by an admin-channel notice."""

    def __init__(self, decision: str, mod: str, user_id: str) -> None:
        label = "Accepter" if decision == "accept" else "Refuser"
        style = discord.ButtonStyle.success if decision == "accept" else discord.ButtonStyle.danger
        super().__init__(
            discord.ui.Button(
                custom_id=f"{STAFF_DECIDE_MARKER}{decision}:{mod}:{user_id}",
                label=label,
                style=style,
            )
        )
        self.decision = decision
        self.mod = mod
        self.user_id = user_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> StaffDecideButton:
        """Rebuild the button from the wire at click time."""
        del interaction, item
        return cls(match.group("decision"), match.group("mod"), match.group("user_id"))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Apply the admin decision (guarded at click time)."""
        await _decide(interaction, self.decision, self.mod, self.user_id)


async def _staff_service(bot: discord.Client) -> StaffService | None:
    """Resolve the wired StaffService from the client (None when unwired)."""
    from kingdoms.discord.bot.factory import KingdomsBot

    if not isinstance(bot, KingdomsBot):
        return None
    return bot.staff_service


async def _season_roles(bot: discord.Client) -> SeasonRolesService | None:
    """Resolve the wired SeasonRolesService from the client."""
    from kingdoms.discord.bot.factory import KingdomsBot

    if not isinstance(bot, KingdomsBot):
        return None
    return bot.season_roles_service


async def _apply(interaction: discord.Interaction, mod: str, message: str = "") -> None:
    """Record the application and answer ephemerally."""
    from kingdoms.discord.bot.factory import KingdomsBot

    bot = interaction.client
    if not isinstance(bot, KingdomsBot) or bot.staff_service is None:
        await interaction.response.send_message("Le staff n'est pas configuré ici.", ephemeral=True)
        return
    staff = bot.staff_service
    if staff is None:
        await interaction.response.send_message("Le staff n'est pas configuré ici.", ephemeral=True)
        return
    guild_id = str(interaction.guild_id) if interaction.guild_id is not None else ""
    user_id = str(interaction.user.id)
    await staff.apply(guild_id, mod, user_id, now=_now_ms(), message=message)
    await interaction.response.send_message(
        f"Candidature staff **{mod}** enregistrée — les admins sont notifiés.",
        ephemeral=True,
    )


async def _nominate(interaction: discord.Interaction, member: discord.User) -> None:
    """Appoint one member to the staff (admin-only, context menu)."""
    from kingdoms.discord.bot.factory import KingdomsBot
    from kingdoms.discord.guards import require_admin

    bot = interaction.client
    if not isinstance(bot, KingdomsBot) or bot.staff_service is None:
        await interaction.response.send_message("Le staff n'est pas configuré ici.", ephemeral=True)
        return
    staff = bot.staff_service
    admins = tuple(bot.status_service.bot_admins)
    allowed = await require_admin(interaction, admins, bot.roles_service)
    if not allowed:
        return
    guild_id = str(interaction.guild_id) if interaction.guild_id is not None else ""
    user_id = str(member.id)
    await staff.apply(guild_id, STAFF_DEFAULT_MOD, user_id, now=_now_ms())
    await staff.decide(
        guild_id, STAFF_DEFAULT_MOD, user_id, accept=True, decided_by=str(interaction.user.id), now=_now_ms()
    )
    season_roles = bot.season_roles_service
    if season_roles is not None:
        season = await _active_season_label(bot, guild_id)
        await season_roles.sync_staff_role(guild_id, user_id, season, member=True)
    await interaction.response.send_message(
        f"<@{user_id}> est nommé staff {STAFF_DEFAULT_MOD}.", ephemeral=True
    )


async def _decide(interaction: discord.Interaction, decision: str, mod: str, user_id: str) -> None:
    """Apply an admin decision on one application (guard at click time)."""
    from kingdoms.discord.bot.factory import KingdomsBot
    from kingdoms.discord.guards import require_admin

    bot = interaction.client
    if not isinstance(bot, KingdomsBot):
        return
    staff = bot.staff_service
    if staff is None:
        await interaction.response.send_message("Le staff n'est pas configuré ici.", ephemeral=True)
        return
    admins = tuple(bot.status_service.bot_admins)
    allowed = await require_admin(interaction, admins, bot.roles_service)
    if not allowed:
        return
    guild_id = str(interaction.guild_id) if interaction.guild_id is not None else ""
    accept = decision == "accept"
    updated = await staff.decide(
        guild_id, mod, user_id, accept=accept, decided_by=str(interaction.user.id), now=_now_ms()
    )
    if updated is None:
        await interaction.response.send_message("Aucune candidature en attente pour ce membre.", ephemeral=True)
        return
    season_roles = bot.season_roles_service
    if accept and season_roles is not None:
        season = await _active_season_label(bot, guild_id)
        await season_roles.sync_staff_role(guild_id, user_id, season, member=True)
    verb = "accepté" if accept else "refusé"
    await interaction.response.edit_message(
        view=build_staff_resolved_notice(mod, user_id, verb, str(interaction.user.id))
    )
    await interaction.followup.send(f"<@{user_id}> est {verb} dans le staff {mod}.", ephemeral=True)


async def _active_season_label(bot: Any, guild_id: str) -> str:
    del guild_id
    """Read the active season label of the default ladder (best-effort, 's1')."""
    try:
        from kingdoms.core.services.season_roles import season_label

        season_service = getattr(bot, "season_service", None)
        ladder_id = getattr(bot, "_ladder_id", None)
        if season_service is None or ladder_id is None:
            return "s1"
        season = await season_service.get_active_season(ladder_id)
        if season is None:
            return "s1"
        return season_label({"name": season.name, "season_id": season.id})
    except Exception:
        return "s1"


def _now_ms() -> int:
    import time

    return int(time.time() * 1000)


def build_staff_notice(mod: str, user_id: str, message: str = "") -> discord.ui.LayoutView:
    """Build the admin notice: the application + the decision buttons."""
    view = discord.ui.LayoutView(timeout=None)
    row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    row.add_item(StaffDecideButton("accept", mod, user_id))
    row.add_item(StaffDecideButton("decline", mod, user_id))
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay(_notice_body(mod, user_id, message)),
        row,
    ))
    return view


def _notice_body(mod: str, user_id: str, message: str) -> str:
    """Format the notice body, quoting the applicant's message."""
    body = f"## 🛡️ Candidature staff **{mod}**\n<@{user_id}> a candidaté."
    if message:
        body += f"\n\n> {message}"
    return body


def build_staff_resolved_notice(mod: str, user_id: str, verb: str, decided_by: str) -> discord.ui.LayoutView:
    """Build the post-decision notice: no interactive buttons left."""
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay(
            f"## 🛡️ Candidature staff **{mod}**\n<@{user_id}> — **{verb}** par <@{decided_by}>."
        ),
    ))
    return view


def register_staff_surface(
    tree: app_commands.CommandTree[discord.Client],
    bot: discord.Client,
) -> None:
    """Register the staff context menu and the persistent buttons on the bot."""

    @app_commands.context_menu(name="Nommer staff")
    async def staff_nominate_menu(interaction: discord.Interaction, member: discord.User) -> None:
        """Right-click on a member: an admin appoints them to the staff."""
        await _nominate(interaction, member)

    tree.add_command(staff_nominate_menu)

    from kingdoms.discord.bot.factory import KingdomsBot

    if isinstance(bot, KingdomsBot):
        bot.add_dynamic_items(StaffApplyButton)
        bot.add_dynamic_items(StaffDecideButton)
