"""Persistent Kingdoms panel components: restart-proof via custom_id.

The Kingdoms pinned surfaces (the Postuler apply panel, the
Candidatures decision rows, the profile action rows, the Demandes
decision rows) are **permanent messages**: they must answer clicks
after every restart, but closures die with the process. Every
component here follows the §3b state reconstruction contract — all
state rides the custom_id (``kingdoms:<component>:<payload>``) and
services resolve from the live bot at click time.

Session-scoped surfaces (the ephemeral role select, kingdom select,
submit/cancel buttons of one enrollment flow) stay live views: they
ride an ephemeral message that expires with the process anyway.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import discord

logger = logging.getLogger("kingdoms.kingdom_persistent")

__all__ = [
    "KingdomApplyButton",
    "KingdomCandidatureButton",
    "KingdomProfileButton",
    "KingdomRequestButton",
    "KingdomsPanelWiring",
    "register_kingdoms_panel_bot",
]


@dataclass(frozen=True, slots=True)
class KingdomsPanelWiring:
    """The services a reconstructed Kingdoms component needs at click time."""

    logs_service: Any = None
    bot_admins: tuple[str, ...] = ()
    mod_roles_service: Any = None
    kingdoms_service: Any = None


_WIRING_RESOLVER: Callable[[], KingdomsPanelWiring] | None = None


def _wiring() -> KingdomsPanelWiring:
    return _WIRING_RESOLVER() if _WIRING_RESOLVER is not None else KingdomsPanelWiring()


def register_kingdoms_panel_wiring(wiring: KingdomsPanelWiring) -> None:
    """Register a static wiring (used by tests and local runs)."""
    global _WIRING_RESOLVER

    def _resolve() -> KingdomsPanelWiring:
        return wiring

    _WIRING_RESOLVER = _resolve


def register_kingdoms_panel_bot(bot: Any) -> None:
    """Register the bot as the wiring source (resolved at click time)."""
    global _WIRING_RESOLVER

    def _resolve() -> KingdomsPanelWiring:
        status = getattr(bot, "status_service", None)
        return KingdomsPanelWiring(
            logs_service=getattr(bot, "logs_service", None),
            bot_admins=tuple(getattr(status, "bot_admins", ())),
            mod_roles_service=getattr(bot, "mod_roles_service", None),
            kingdoms_service=getattr(bot, "kingdoms_service", None),
        )

    _WIRING_RESOLVER = _resolve


def register_kingdoms_persistent_items(bot: discord.Client) -> None:
    """Re-register every persistent Kingdoms component class on the bot."""
    bot.add_dynamic_items(
        KingdomApplyButton,
        KingdomCandidatureButton,
        KingdomProfileButton,
        KingdomRequestButton,
        KingdomAdminButton,
    )


def _is_admin(interaction: discord.Interaction, bot_admins: tuple[str, ...]) -> bool:
    user_id = getattr(interaction.user, "id", None)
    if user_id is not None and str(user_id) in bot_admins:
        return True
    permissions = getattr(interaction.user, "guild_permissions", None)
    return bool(permissions and permissions.administrator)


def _strings(locale: str) -> dict[str, str]:
    from kingdoms.discord.kingdom_panels import _strings as panels_strings

    return panels_strings(locale)


class KingdomApplyButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:apply:open",
):
    """The restart-proof Enroll button of the pinned Postuler panel."""

    def __init__(self, label: str = "📋 S'inscrire", style: discord.ButtonStyle = discord.ButtonStyle.primary) -> None:
        super().__init__(
            discord.ui.Button(
                label=label,
                custom_id="kingdoms:apply:open",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomApplyButton:
        """Rebuild the button from the wire — the only post-restart path."""
        locale = str(interaction.locale) if interaction.locale else "en"
        strings = _strings(locale)
        return cls(label=strings["apply_button"][:80])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the ephemeral enrollment flow, fresh from the wiring."""
        from kingdoms.discord.kingdom_panels import _ApplicationContext, _role_select_view

        wiring = _wiring()
        guild = interaction.guild
        candidatures = None
        if guild is not None:
            candidatures = next(
                (c for c in guild.text_channels if c.name.lower() == "candidatures"),
                None,
            )
        context = _ApplicationContext(
            locale=str(interaction.locale) if interaction.locale else "en",
            candidatures_channel=candidatures,
            bot_admins=wiring.bot_admins,
            mod_roles_service=wiring.mod_roles_service,
            guild_id=str(guild.id) if guild is not None else "",
            kingdoms_service=wiring.kingdoms_service,
        )
        strings = _strings(context.locale)
        await interaction.response.send_message(
            f"**{strings['choose_role']}**",
            view=_role_select_view(context),
            ephemeral=True,
        )


class KingdomCandidatureButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:candidature:(?P<decision>approve|pending|refuse)",
):
    """The restart-proof decision button of one candidature message."""

    def __init__(self, decision: str, emoji: str, style: discord.ButtonStyle) -> None:
        custom_id = f"kingdoms:candidature:{decision}"
        super().__init__(
            discord.ui.Button(
                emoji=emoji,
                custom_id=custom_id,
            )
        )
        self.decision = decision

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomCandidatureButton:
        """Rebuild the decision button from the wire."""
        decision = match.group("decision")
        styles = {
            "approve": (discord.ButtonStyle.success, "✅"),
            "pending": (discord.ButtonStyle.secondary, "⏳"),
            "refuse": (discord.ButtonStyle.danger, "❌"),
        }
        style, emoji = styles[decision]
        return cls(decision, emoji, style)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Apply the decision through the live wiring (roles, notes)."""
        from kingdoms.discord.kingdom_panels import KINGDOM_MOD, ROLE_KING, ROLE_LORD

        wiring = _wiring()
        if not _is_admin(interaction, wiring.bot_admins):
            await interaction.response.send_message("Only admins can decide on applications.", ephemeral=True)
            return
        locale = str(interaction.locale) if interaction.locale else "en"
        strings = _strings(locale)
        label = {
            "approve": strings["approved"],
            "pending": strings["pending"],
            "refuse": strings["refused"],
        }[self.decision]
        content = interaction.message.content if interaction.message is not None else ""
        await interaction.response.edit_message(content=content, view=None)
        note = strings["decided"].format(label)
        if self.decision == "approve":
            applicant = re.search(r"<@(\d+)>", content)
            is_king = strings["role_king"] in content
            role_key = ROLE_KING if is_king else ROLE_LORD
            if wiring.mod_roles_service is not None and applicant is not None and interaction.guild is not None:
                try:
                    await wiring.mod_roles_service.assign_mod_role(
                        str(interaction.guild.id), applicant.group(1), KINGDOM_MOD, role_key
                    )
                    note += " " + strings["role_assigned"].format(role_key)
                except Exception:
                    logger.warning("CANDIDATURES: role assignment failed", exc_info=True)
                    note += " " + strings["no_service"]
        await interaction.followup.send(note, ephemeral=True)


class KingdomProfileButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:profile:(?P<action>edit|stats|success|leave)",
):
    """The restart-proof action button of one profile message."""

    def __init__(self, action: str, label: str, style: discord.ButtonStyle) -> None:
        super().__init__(
            discord.ui.Button(
                label=label,
                custom_id=f"kingdoms:profile:{action}",
            )
        )
        self.action = action

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomProfileButton:
        """Rebuild the profile button from the wire."""
        from kingdoms.discord.kingdom_profiles import _strings as profile_strings

        locale = str(interaction.locale) if interaction.locale else "en"
        strings = profile_strings(locale)
        action = match.group("action")
        labels = {
            "edit": strings["edit_button"],
            "stats": strings["stats_button"],
            "success": strings["success_button"],
            "leave": strings["leave_button"],
        }
        style = discord.ButtonStyle.primary if action == "edit" else discord.ButtonStyle.secondary
        return cls(action, labels[action][:80], style)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Run the profile action with the live wiring."""
        from kingdoms.discord.kingdom_profiles import (
            _LeaveModal,
            _relay_request,
        )
        from kingdoms.discord.kingdom_profiles import (
            _strings as profile_strings,
        )

        locale = str(interaction.locale) if interaction.locale else "en"
        strings = profile_strings(locale)
        if self.action in ("stats", "success"):
            await interaction.response.send_message(strings["coming_soon"], ephemeral=True)
            return
        if self.action == "leave":
            await interaction.response.send_modal(_LeaveModal(locale))
            return
        message_content = interaction.message.content if interaction.message is not None else ""
        validated = strings["state_validated"] in message_content
        if not validated:
            await interaction.response.send_message(strings["edit_pending"], ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await _relay_request(
            interaction,
            locale,
            title=strings["edit_request_title"],
            custom_prefix="edit",
        )
        await interaction.followup.send(
            f"{strings['edit_validated']}\n{strings['edit_requested']}", ephemeral=True
        )


class KingdomRequestButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:request:(?P<kind>leave|edit)-(?P<verdict>approve|refuse)",
):
    """The restart-proof decision button of one relayed admin request."""

    def __init__(self, kind: str, verdict: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="✅" if verdict == "approve" else "❌",
                style=discord.ButtonStyle.success if verdict == "approve" else discord.ButtonStyle.danger,
                custom_id=f"kingdoms:request:{kind}-{verdict}",
            )
        )
        self.kind = kind
        self.verdict = verdict

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomRequestButton:
        """Rebuild the request decision button from the wire."""
        return cls(match.group("kind"), match.group("verdict"))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Close the request and annotate the message."""
        from kingdoms.discord.kingdom_profiles import _strings as profile_strings

        wiring = _wiring()
        if not _is_admin(interaction, wiring.bot_admins):
            await interaction.response.send_message("Only admins can decide.", ephemeral=True)
            return
        locale = str(interaction.locale) if interaction.locale else "en"
        strings = profile_strings(locale)
        approved = self.verdict == "approve"
        note = strings[f"{self.kind}_{'approved' if approved else 'refused'}"]
        content = interaction.message.content if interaction.message is not None else ""
        await interaction.response.edit_message(content=f"{content}\n\n**{note}**", view=None)

class KingdomRemovePlayerModal(discord.ui.Modal):
    """The admin form to remove one enrolled player (by mention/id)."""

    player: discord.ui.TextInput[KingdomRemovePlayerModal] = discord.ui.TextInput(
        label="Player (mention or id)",
        placeholder="@player",
        max_length=32,
        required=True,
    )

    def __init__(self, locale: str) -> None:
        self.locale = "fr" if str(locale).lower().startswith("fr") else "en"
        strings = _profile_strings(self.locale)
        self.player.label = strings["remove_player_field"][:45]
        super().__init__(title=strings["remove_player_title"][:45], timeout=300)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        strings = _profile_strings(self.locale)
        raw = (self.player.value or "").strip()
        match = re.search(r"<@!?(\d+)>", raw) or re.search(r"^(\d{6,25})$", raw)
        if match is None:
            await interaction.response.send_message(strings["remove_player_not_found"], ephemeral=True)
            return
        user_id = match.group(1)
        wiring = _wiring()
        guild = interaction.guild
        if wiring.mod_roles_service is not None and guild is not None:
            for role_key in ("kingdoms_lord", "kingdoms_king"):
                try:
                    await wiring.mod_roles_service.remove_mod_role(
                        str(guild.id), user_id, "kingdoms", role_key
                    )
                except Exception:
                    logger.warning("KINGDOMS ADMIN: role removal failed for %s", role_key, exc_info=True)
        note = strings["remove_player_done"].format(f"<@{user_id}>")
        await interaction.response.send_message(note, ephemeral=True)


def _profile_strings(locale: str) -> dict[str, str]:
    from kingdoms.discord.kingdom_profiles import _strings as ps

    return ps(locale)


class KingdomAdminButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:admin:(?P<action>remove|reset|reset-confirm|reset-cancel)",
):
    """The restart-proof admin buttons of the Param\u00e8tres panel."""

    def __init__(self, action: str, label: str, style: discord.ButtonStyle) -> None:
        super().__init__(
            discord.ui.Button(
                label=label,
                custom_id=f"kingdoms:admin:{action}",
            )
        )
        self.action = action

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomAdminButton:
        """Rebuild the admin button from the wire."""
        locale = str(interaction.locale) if interaction.locale else "en"
        strings = _profile_strings(locale)
        action = match.group("action")
        labels = {
            "remove": strings["remove_player_button"],
            "reset": strings["reset_salons_button"],
            "reset-confirm": strings["reset_confirm_button"],
            "reset-cancel": strings["reset_cancel_button"],
        }
        styles = {
            "remove": discord.ButtonStyle.danger,
            "reset": discord.ButtonStyle.danger,
            "reset-confirm": discord.ButtonStyle.success,
            "reset-cancel": discord.ButtonStyle.secondary,
        }
        return cls(action, labels[action][:80], styles[action])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Run the admin action (remove player or reset salons)."""
        locale = str(interaction.locale) if interaction.locale else "en"
        strings = _profile_strings(locale)
        wiring = _wiring()
        if not _is_admin(interaction, wiring.bot_admins):
            await interaction.response.send_message("Only admins can use this panel.", ephemeral=True)
            return
        if self.action == "remove":
            await interaction.response.send_modal(KingdomRemovePlayerModal(locale))
            return
        if self.action == "reset":
            await _ask_reset_confirmation(interaction, strings)
            return
        if self.action == "reset-cancel":
            await interaction.response.edit_message(content=strings["reset_cancelled"], view=None)
            return
        await _run_reset(interaction, strings)

    @staticmethod
    def _confirm_button(verdict: str, label: str, style: discord.ButtonStyle) -> KingdomAdminButton:
        return KingdomAdminButton(verdict, label[:80], style)

async def _ask_reset_confirmation(interaction: discord.Interaction, strings: dict[str, str]) -> None:
    """Ask the admin to confirm the salons reset."""
    view = discord.ui.View(timeout=120)
    for verdict, label_key, style in (
        ("reset-confirm", "reset_confirm_button", discord.ButtonStyle.success),
        ("reset-cancel", "reset_cancel_button", discord.ButtonStyle.secondary),
    ):
        view.add_item(KingdomAdminButton(verdict, strings[label_key][:80], style))
    await interaction.response.send_message(
        f"**{strings['reset_confirm_title']}**\n{strings['reset_confirm_hint']}",
        view=view,
        ephemeral=True,
    )


async def _run_reset(interaction: discord.Interaction, strings: dict[str, str]) -> None:
    """Delete every kingdoms channel/category, then report."""
    from kingdoms.discord.kingdom_setup import SALONS_FIRST_STRUCTURE

    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(strings["no_channel"], ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    deleted = 0
    try:
        structure_names: set[str] = set()
        for category_name, channel_names, _ in SALONS_FIRST_STRUCTURE:
            structure_names.add(category_name.lower())
            structure_names.update(ch.lower() for ch in channel_names)
        channels = list(guild.text_channels)
        categories = list(getattr(guild, "categories", []))
        for channel in channels:
            if channel.name.lower() in structure_names or channel.name.lower().startswith("profil-"):
                try:
                    await channel.delete()
                    deleted += 1
                except Exception:
                    logger.warning("KINGDOMS ADMIN: channel delete failed", exc_info=True)
        for category in categories:
            if category.name.lower() in structure_names:
                try:
                    await category.delete()
                    deleted += 1
                except Exception:
                    logger.warning("KINGDOMS ADMIN: category delete failed", exc_info=True)
    except Exception:
        logger.exception("KINGDOMS ADMIN: salons reset failed for guild %s", guild.id)
        await interaction.followup.send(strings["reset_failed"], ephemeral=True)
        return
    await interaction.followup.send(strings["reset_done"].format(deleted), ephemeral=True)
