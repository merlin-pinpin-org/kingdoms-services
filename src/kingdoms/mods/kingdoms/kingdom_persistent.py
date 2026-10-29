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

import io
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from typing import Any, cast
from zoneinfo import ZoneInfo

import discord

logger = logging.getLogger("kingdoms.kingdom_persistent")

__all__ = [
    "KingdomApplyButton",
    "KingdomCandidatureButton",
    "KingdomKingClaimSelect",
    "KingdomKingNameButton",
    "KingdomKingNameModal",
    "KingdomMarketActionButton",
    "KingdomMarketCancelButton",
    "KingdomMarketConfirmButton",
    "KingdomMarketLedgerButton",
    "KingdomMarketModal",
    "KingdomMarketTechButton",
    "KingdomProfileButton",
    "KingdomRempartsOpenButton",
    "KingdomRequestButton",
    "KingdomRoleInfoButton",
    "KingdomSeigneurieOpenButton",
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
    channel_service: Any = None
    registry: Any = None
    territories_service: Any = None
    attacks_service: Any = None
    economy_service: Any = None
    diplomacy_service: Any = None
    events_service: Any = None
    time_service: Any = None
    distributor_service: Any = None
    voliere_service: Any = None
    tavern_service: Any = None
    mercenary_service: Any = None
    ffa_service: Any = None
    cathedral_service: Any = None


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
        # 2026-10-24 live incident: an unwired boot (service build
        # failed) left every panel working but the enrollments, king
        # DMs and console dead. Every wiring resolution self-heals the
        # service stack before reading it.
        try:
            from kingdoms.mods.kingdoms import ensure_kingdoms_services

            ensure_kingdoms_services(bot)
        except Exception:
            logger.exception("KINGDOMS PANELS: on-demand kingdoms service rebuild crashed")
        status = getattr(bot, "status_service", None)
        return KingdomsPanelWiring(
            logs_service=getattr(bot, "logs_service", None),
            bot_admins=tuple(getattr(status, "bot_admins", ())),
            mod_roles_service=getattr(bot, "mod_roles_service", None),
            kingdoms_service=getattr(bot, "kingdoms_service", None),
            channel_service=getattr(bot, "channel_service", None),
            registry=getattr(bot, "registry", None),
            territories_service=getattr(bot, "kingdoms_territories_service", None),
            attacks_service=getattr(bot, "kingdoms_attacks_service", None),
            economy_service=getattr(bot, "kingdoms_economy_service", None),
            diplomacy_service=getattr(bot, "kingdoms_diplomacy_service", None),
            events_service=getattr(bot, "kingdoms_events_service", None),
            time_service=getattr(bot, "kingdoms_time_service", None),
            distributor_service=getattr(bot, "kingdoms_distributor_service", None),
            voliere_service=getattr(bot, "kingdoms_voliere_service", None),
            tavern_service=getattr(bot, "kingdoms_tavern_service", None),
            mercenary_service=getattr(bot, "kingdoms_mercenary_service", None),
            ffa_service=getattr(bot, "kingdoms_ffa_service", None),
            cathedral_service=getattr(bot, "kingdoms_cathedral_service", None),
        )

    _WIRING_RESOLVER = _resolve


def register_kingdoms_persistent_items(bot: discord.Client) -> None:
    """Re-register every persistent Kingdoms component class on the bot."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        KingdomAttackDeclareButton,
        KingdomAttackDefendButton,
        KingdomAttackJoinButton,
        KingdomAttackInfoButton,
        KingdomAttackManagerButton,
        KingdomDefenseManagerButton,
        KingdomDraftViewButton,
        KingdomPlanButton,
        KingdomPlanCancelButton,
        KingdomRoomResultButton,
        KingdomRoomUrlButton,
        KingdomSabotageOpenButton,
        KingdomSabotagePassButton,
    )
    from kingdoms.mods.kingdoms.kingdom_distributor_panel import KingdomDistributorButton
    from kingdoms.mods.kingdoms.kingdom_scriptorium import (
        KingdomSeigneurieButton,
        KingdomSeigneurieSelect,
    )
    from kingdoms.mods.kingdoms.kingdom_mercenary_panel import (
        KingdomMercenaryBidButton,
        KingdomMercenaryCallButton,
        KingdomMercenaryWithdrawButton,
    )
    from kingdoms.mods.kingdoms.kingdom_tavern_panel import (
        KingdomTavernDrinkButton,
        KingdomTavernInfoButton,
        KingdomTavernMenuButton,
        KingdomTavernPurseButton,
    )
    from kingdoms.mods.kingdoms.tavern_immersion import (
        KingdomSoudoyeurRevealButton,
        KingdomTavernEnterButton,
        KingdomTavernExitButton,
    )
    from kingdoms.mods.kingdoms.kingdom_alliances_panel import (
        KingdomAlliancesDurationsButton,
        KingdomAlliancesHistoryButton,
        KingdomAlliancesLockedButton,
        KingdomAlliancesNamesButton,
        KingdomAlliancesNextButton,
    )
    from kingdoms.mods.kingdoms.kingdom_time_panel import KingdomTimeButton
    from kingdoms.mods.kingdoms.kingdom_voliere_panel import (
        KingdomTheftButton,
        KingdomVoliereButton,
    )

    bot.add_dynamic_items(
        KingdomVoliereButton,
        KingdomTheftButton,
        KingdomRempartsOpenButton,
        KingdomSeigneurieOpenButton,
        KingdomSeigneurieButton,
        KingdomSeigneurieSelect,
        KingdomAttackDeclareButton,
        KingdomAttackDefendButton,
        KingdomAttackJoinButton,
        KingdomAttackInfoButton,
        KingdomAttackManagerButton,
        KingdomDefenseManagerButton,
        KingdomDraftViewButton,
        # planification-attaque-v1 (Drasah 23/10): the planning
        # wizard, the kingdom's plan list and the plan cancel.
        KingdomPlanButton,
        KingdomPlanCancelButton,
        KingdomRoomResultButton,
        KingdomRoomUrlButton,
        KingdomSabotageOpenButton,
        KingdomSabotagePassButton,
        KingdomApplyButton,
        KingdomCandidatureButton,
        KingdomProfileButton,
        KingdomRequestButton,
        KingdomAdminButton,
        KingdomMarketTechButton,
        KingdomMarketActionButton,
        KingdomMarketLedgerButton,
        KingdomMarketConfirmButton,
        KingdomMarketCancelButton,
        KingdomRempartsGuardButton,
        KingdomRempartsPatrouilleButton,
        KingdomFFAJoinButton,
        KingdomRealmButton,
        KingdomKingNameButton,
        KingdomKingClaimSelect,
        KingdomRoleInfoButton,
        KingdomTimeButton,
        KingdomChurchMarryButton,
        KingdomChurchArrangeButton,
        KingdomChurchUpgradeButton,
        KingdomChurchConsecrateButton,
        KingdomDistributorButton,
        KingdomTavernDrinkButton,
        # taverne-immersion-v1 (Drasah 26/10): the taverne facade's
        # door-in button and the comptoir's door-out button.
        KingdomTavernEnterButton,
        KingdomTavernExitButton,
        # taverne-soudoyeur-v1 (Drasah 26/10): the comptoir's
        # "ask to see the Soudoyeur" reveal button.
        KingdomSoudoyeurRevealButton,
        # taverne-comptoir-v2 (Drasah 26/10): the comptoir pin's
        # drinks menu button (ephemeral interface).
        KingdomTavernMenuButton,
        # taverne-comptoir-v3 (Drasah 26/10): the comptoir pin's
        # information and purse buttons (ephemerals).
        KingdomTavernInfoButton,
        KingdomTavernPurseButton,
        # chef-mercenaire-v1 T2b (Drasah 25/10): the Chief's bid and
        # withdraw buttons of the taverne panel.
        KingdomMercenaryBidButton,
        KingdomMercenaryWithdrawButton,
        # taverne-chef-beta-call (Drasah 25/10): the admin-only beta
        # button that calls the Chief at ANY moment from the taverne.
        KingdomMercenaryCallButton,
        # alliances-lisibilite-v1 T2 (Drasah 25/10): the five
        # ephemeral answers of the alliances panel.
        KingdomAlliancesNextButton,
        KingdomAlliancesLockedButton,
        KingdomAlliancesHistoryButton,
        KingdomAlliancesDurationsButton,
        KingdomAlliancesNamesButton,
    )


def _is_admin(interaction: discord.Interaction, bot_admins: tuple[str, ...]) -> bool:
    user_id = getattr(interaction.user, "id", None)
    if user_id is not None and str(user_id) in bot_admins:
        return True
    permissions = getattr(interaction.user, "guild_permissions", None)
    return bool(permissions and permissions.administrator)


def _strings(locale: str) -> dict[str, Any]:
    """Merge the panel strings with the realm-content view strings.

    The church buttons are rebuilt from the wire (from_custom_id) with
    THESE strings - their labels (church_marry_button & co) live in the
    realm-content catalog, not in the panels one. A missing key crashed
    the rebuild with a silent KeyError (Drasah 2026-10-11 feedback).
    """
    from kingdoms.mods.kingdoms.kingdom_panels import _strings as panels_strings
    from kingdoms.mods.kingdoms.kingdom_realm_content import _strings as realm_strings

    merged: dict[str, Any] = dict(realm_strings(locale))
    merged.update(panels_strings(locale))
    return merged


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
        from kingdoms.mods.kingdoms.kingdom_panels import _ApplicationContext, _role_select_view

        wiring = _wiring()
        guild = interaction.guild
        candidatures = None
        if guild is not None:
            from kingdoms.mods.kingdoms.kingdom_setup import _slug

            candidatures = next(
                (c for c in guild.text_channels if _slug(c.name) == "candidatures"),
                None,
            )
        context = _ApplicationContext(
            locale=str(interaction.locale) if interaction.locale else "en",
            candidatures_channel=candidatures,
            bot_admins=wiring.bot_admins,
            mod_roles_service=wiring.mod_roles_service,
            guild_id=str(guild.id) if guild is not None else "",
            kingdoms_service=wiring.kingdoms_service,
            anchor=interaction,
        )
        strings = _strings(context.locale)
        await interaction.response.send_message(
            f"**{strings['choose_role']}**",
            view=_role_select_view(context),
            ephemeral=True,
        )


class KingdomRoleInfoButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:apply:roleinfo:(?P<role>king|lord)",
):
    """The restart-proof “View the role” sheet button of the Postuler panel."""

    def __init__(self, role: str, label: str, style: discord.ButtonStyle = discord.ButtonStyle.secondary) -> None:
        custom_id = f"kingdoms:apply:roleinfo:{role}"
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                style=style,
                custom_id=custom_id,
            )
        )
        self.role = role

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomRoleInfoButton:
        """Rebuild the role sheet button from the wire — post-restart path."""
        locale = str(interaction.locale) if interaction.locale else "en"
        strings = _strings(locale)
        role = match.group("role")
        key = "roleinfo_king_button" if role == "king" else "roleinfo_lord_button"
        return cls(role, strings[key])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Send the one-page rights & obligations sheet, ephemeral."""
        locale = str(interaction.locale) if interaction.locale else "en"
        strings = _strings(locale)
        key = "role_sheet_king" if self.role == "king" else "role_sheet_lord"
        # fenetre-unique-v2 (Drasah 27/10): one ephemeral window at a time
        # — opening a sheet closes the player's previous window (his live
        # report: the Roi and Seigneur sheets stacked below the panel).
        user_id = str(getattr(interaction.user, "id", ""))
        await _close_previous_ephemeral(user_id)
        await interaction.response.send_message(strings[key], ephemeral=True)
        _EPHEMERAL_WINDOWS[user_id] = (interaction, time.monotonic())


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
        """Apply the decision through the live wiring (roles, enrollment, notes)."""
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
            applicant = re.search(r"<@!?(\d+)>", content)
            is_king = strings["role_king"] in content
            kingdom_name = _candidature_kingdom_name(content, strings)
            note += await _apply_candidature_approval(
                interaction,
                wiring,
                strings,
                locale,
                applicant_id=applicant.group(1) if applicant is not None else "",
                is_king=is_king,
                kingdom_name=kingdom_name,
            )
        await interaction.followup.send(note, ephemeral=True)


async def _apply_candidature_approval(
    interaction: discord.Interaction,
    wiring: KingdomsPanelWiring,
    strings: dict[str, Any],
    locale: str,
    *,
    applicant_id: str,
    is_king: bool,
    kingdom_name: str | None,
) -> str:
    """Enroll, grant the mod role and announce one approved candidature.

    The enrollment stores the member's live display name — a raw
    ``<@id>`` mention used to leak into every select label (Drasah's
    2026-10-11 report).
    """
    from kingdoms.mods.kingdoms.kingdom_panels import KINGDOM_MOD, ROLE_KING, ROLE_LORD

    guild = interaction.guild
    role_key = ROLE_KING if is_king else ROLE_LORD
    extra = ""
    enrolled = False
    applicant_display: str | None = None
    if applicant_id and guild is not None:
        applicant_display = await _member_display_name_safe(guild, applicant_id)
    if applicant_id and wiring.kingdoms_service is not None and guild is not None:
        enrollment_note, enrolled = await _enroll_applicant(
            wiring.kingdoms_service,
            applicant_id,
            is_king=is_king,
            kingdom_name=kingdom_name,
            strings=strings,
            display_name=applicant_display,
        )
        extra += " " + enrollment_note
    elif applicant_id and wiring.kingdoms_service is None:
        # 2026-10-24 live incident: an unwired mod made the approval
        # "succeed" (role assigned) while nothing was enrolled — the
        # player never heard from the bot again. Never silent again.
        logger.error(
            "CANDIDATURES: kingdoms service UNWIRED — enrollment skipped for %s"
            " (the player was told)",
            applicant_id,
        )
        extra += " " + strings["enroll_service_down"]
    if wiring.mod_roles_service is not None and applicant_id and guild is not None:
        try:
            await wiring.mod_roles_service.assign_mod_role(
                str(guild.id), applicant_id, KINGDOM_MOD, role_key
            )
            extra += " " + strings["role_assigned"].format(role_key)
        except Exception:
            logger.warning("CANDIDATURES: role assignment failed", exc_info=True)
            extra += " " + strings["no_service"]
    # validation-immersion-v1 (Drasah 28/10, live report Rakussy): an
    # approved candidate's out-of-game opt-in (a MEMBER allow over
    # Beta/Conscription that beats the role deny) dies with his
    # validation — « Postuler » hides, the immersion default takes over.
    if guild is not None and applicant_id:
        try:
            from kingdoms.mods.kingdoms.kingdom_immersion import clear_outgame_allow_safe

            await clear_outgame_allow_safe(guild, applicant_id)
        except Exception:
            logger.warning("CANDIDATURES: outgame allow clear failed", exc_info=True)
    # Live incident (Drasah 26/10, Aubin):
    # a LORD approved mid-season is enrolled and granted the kingdoms_lord
    # role, but the realm categories are gated by MEMBER-level overwrites
    # (not the role) — without this re-sync the newcomer sees Territoire
    # and Alliances of every realm yet NOT his own 🏰 kingdom category.
    # Same class as the 2026-10-18 King-claim incident: targeted grant
    # first, blanket re-sync as the fallback.
    if enrolled and kingdom_name and guild is not None and applicant_id:
        if wiring.kingdoms_service is not None and str(applicant_id).isdigit():
            try:
                from kingdoms.mods.kingdoms.kingdom_realms import (
                    ensure_all_realm_structures,
                    ensure_member_realm_access,
                )

                kingdom = next(
                    (
                        k
                        for k in await wiring.kingdoms_service.kingdoms()
                        if str(k.name).strip().casefold()
                        == str(kingdom_name).strip().casefold()
                    ),
                    None,
                )
                if kingdom is not None:
                    granted = await ensure_member_realm_access(
                        guild, kingdom, int(applicant_id)
                    )
                    if not granted:
                        await ensure_all_realm_structures(
                            guild, wiring.kingdoms_service
                        )
            except Exception:
                logger.warning(
                    "CANDIDATURES: realm access re-sync failed after approval",
                    exc_info=True,
                )
    if guild is not None and applicant_id:
        await _send_welcome(guild, applicant_id, strings)
        if is_king and kingdom_name is None:
            await _send_king_kingdom_request(guild, applicant_id, locale)
    if guild is not None:
        await _refresh_season_status_safe(guild, locale)
        if enrolled:
            await _announce_enrollment_safe(
                guild, locale, f"<@{applicant_id}>", kingdom_name, is_king
            )
    return extra


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
        from kingdoms.mods.kingdoms.kingdom_profiles import _strings as profile_strings

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
        from kingdoms.mods.kingdoms.kingdom_profiles import (
            _LeaveModal,
            _relay_request,
        )
        from kingdoms.mods.kingdoms.kingdom_profiles import (
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
        from kingdoms.mods.kingdoms.kingdom_profiles import _strings as profile_strings

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
        if self.kind == "leave" and approved:
            # the approval used to be cosmetic — the season data never
            # recorded the departure (throne stayed taken, heirs stuck).
            match = re.search(r"<@!?(\d+)>", content)
            player_id = match.group(1) if match is not None else ""
            applied = await _apply_player_leave_safe(
                interaction.guild, player_id, "leave approved by an admin", locale
            )
            if applied:
                try:
                    await interaction.followup.send(strings["leave_applied"], ephemeral=True)
                except Exception:
                    logger.info("KINGDOMS: leave-applied note failed", exc_info=True)

async def _king_of(kingdoms_service: Any, user_id: str) -> Any | None:
    """Return the active King lord record of the user (None otherwise)."""
    if kingdoms_service is None:
        return None
    from kingdoms.mods.kingdoms.service import KING_ROLE

    try:
        lords = await kingdoms_service.lords()
    except Exception:
        logger.warning("KINGDOMS MARKET: lord lookup failed", exc_info=True)
        return None
    return next(
        (
            lord
            for lord in lords
            if lord.id == user_id
            and lord.role == KING_ROLE
            and not lord.left
            and bool(lord.kingdom_id)
        ),
        None,
    )


def _market_error_note(exc: Exception, strings: dict[str, Any]) -> str:
    """Translate one economy/attack error into a localized market note."""
    from kingdoms.mods.kingdoms.attacks import (
        InsufficientTechPointsError,
        TechnologyLimitReachedError,
    )
    from kingdoms.mods.kingdoms.diplomacy import (
        AlreadyMarriedError,
        CivilizationNotInHandError,
        MarriageError,
        MarriageExclusivityError,
        NoArrangedTokenError,
        NoMarriagePointsError,
        RoyalMarriagePointsError,
        RoyalMarriageTokenError,
    )
    from kingdoms.mods.kingdoms.economy import (
        EconomyLimitReachedError,
        GuardAlreadyActiveError,
        InsufficientPointsError,
        TerritoryProtectedError,
    )
    from kingdoms.mods.kingdoms.service import (
        ParishInsufficientTechError,
        ParishMaxTierError,
    )

    cases: tuple[tuple[tuple[type[Exception], ...], str], ...] = (
        (
            (
                InsufficientPointsError,
                InsufficientTechPointsError,
                ParishInsufficientTechError,
            ),
            "market_err_insufficient",
        ),
        ((EconomyLimitReachedError, TechnologyLimitReachedError), "market_err_limit"),
        ((TerritoryProtectedError,), "market_err_protected"),
        ((GuardAlreadyActiveError,), "market_err_guard_active"),
        ((AlreadyMarriedError,), "market_err_already_married"),
        ((MarriageExclusivityError,), "market_err_civ_taken"),
        ((NoMarriagePointsError,), "market_err_no_points"),
        ((RoyalMarriagePointsError,), "church_err_royal_points"),
        ((RoyalMarriageTokenError,), "church_err_royal_token"),
        ((NoArrangedTokenError,), "market_err_no_token"),
        ((CivilizationNotInHandError,), "church_err_not_in_hand"),
        ((ParishMaxTierError,), "church_upgrade_max"),
        ((MarriageError,), "market_err_civ_unknown"),
    )
    for errors, key in cases:
        if isinstance(exc, errors):
            return str(strings[key])
    logger.warning("KINGDOMS MARKET: purchase failed", exc_info=True)
    return str(strings["market_err_unknown"]).format(type(exc).__name__)


def _market_costs(wiring: KingdomsPanelWiring) -> Any:
    """Resolve the technology costs config with the panel fallback."""
    technologies = None
    if wiring.kingdoms_service is not None:
        technologies = getattr(getattr(wiring.kingdoms_service, "config", None), "technologies", None)
    if technologies is None:
        from kingdoms.mods.kingdoms.config import TechnologyCosts

        technologies = TechnologyCosts()
    return technologies


def _market_limit(wiring: KingdomsPanelWiring, item: str) -> int:
    """Return the configured per-season purchase limit (0 = unlimited)."""
    limits = getattr(_market_costs(wiring), "limits", None) or {}
    return int(limits.get(item, 0) or 0)


async def _market_bought(wiring: KingdomsPanelWiring, kingdom_id: str, item: str) -> int:
    """Return the season purchase counter of one article (0 on failure)."""
    if wiring.attacks_service is None or not kingdom_id:
        return 0
    try:
        state = await wiring.attacks_service.technology_state(kingdom_id)
        purchases = getattr(state, "purchases", None) or {}
        return int(purchases.get(item, 0) or 0)
    except Exception:
        logger.info("KINGDOMS MARKET: purchase counter read failed")
        return 0


async def _market_wallet(wiring: KingdomsPanelWiring, kingdom_id: str) -> int:
    """Return the live treasury balance (0 on failure)."""
    if wiring.economy_service is None or not kingdom_id:
        return 0
    try:
        return int(await wiring.economy_service.wallet(kingdom_id))
    except Exception:
        logger.info("KINGDOMS MARKET: wallet read failed")
        return 0


def _market_label(item: str, strings: dict[str, Any]) -> str:
    """Return the localized article label of one market item."""
    tech = strings.get("market_tech") or {}
    action = strings.get("market_action") or {}
    return str(tech.get(item) or action.get(item) or item)


async def _market_fiche(wiring: KingdomsPanelWiring, king: Any, item: str, strings: dict[str, Any]) -> str:
    """Build the modal article sheet: effect, cost, live treasury, limits."""
    cost = int(getattr(_market_costs(wiring), item, 0) or 0)
    limit = _market_limit(wiring, item)
    kingdom_id = king.kingdom_id or ""
    lines = [
        str(strings["market_fiche_effect"]).format(strings["market_effect"].get(item, "")),
        str(strings["market_fiche_cost"]).format(cost),
        str(strings["market_fiche_wallet"]).format(await _market_wallet(wiring, kingdom_id)),
    ]
    if limit > 0:
        bought = await _market_bought(wiring, kingdom_id, item)
        lines.append(str(strings["market_fiche_left"]).format(max(0, limit - bought), limit))
    lines.append(str(strings["market_fiche_king_only"]))
    return "\n".join(lines)


def _market_ttl(interaction: Any, message: Any) -> None:
    """Best-effort standard TTL on one market ephemeral (marche-v2)."""
    try:
        from kingdoms.mods.kingdoms.kingdom_panels import _schedule_ephemeral_delete

        _schedule_ephemeral_delete(interaction, message)
    except Exception:
        logger.info("KINGDOMS MARKET: ephemeral TTL not scheduled")


async def _market_failure_note(
    exc: Exception,
    wiring: KingdomsPanelWiring,
    king: Any,
    item: str,
    cost: int,
    strings: dict[str, Any],
) -> str:
    """Enriched market errors: the missing amount and the N/N limit."""
    from kingdoms.mods.kingdoms.attacks import (
        InsufficientTechPointsError,
        TechnologyLimitReachedError,
    )
    from kingdoms.mods.kingdoms.economy import EconomyLimitReachedError, InsufficientPointsError

    kingdom_id = king.kingdom_id or ""
    if isinstance(exc, (InsufficientPointsError, InsufficientTechPointsError)):
        missing = max(0, cost - await _market_wallet(wiring, kingdom_id))
        return str(strings["market_err_missing"]).format(missing)
    if isinstance(exc, (EconomyLimitReachedError, TechnologyLimitReachedError)):
        bought = await _market_bought(wiring, kingdom_id, item)
        limit = _market_limit(wiring, item) or bought
        return str(strings["market_err_limit_reached"]).format(bought, limit)
    return _market_error_note(exc, strings)


async def _market_success_note(
    wiring: KingdomsPanelWiring,
    kingdom_id: str,
    item: str,
    strings: dict[str, Any],
    detail: str = "",
) -> str:
    """Build the unique post-purchase echo: bought + treasury + limit."""
    label = _market_label(item, strings)
    wallet = await _market_wallet(wiring, kingdom_id)
    note = (
        str(strings["market_bought_detail"]).format(label, detail, wallet)
        if detail
        else str(strings["market_bought"]).format(label, wallet)
    )
    limit = _market_limit(wiring, item)
    if limit > 0:
        bought = await _market_bought(wiring, kingdom_id, item)
        note += str(strings["market_bought_left"]).format(max(0, limit - bought), limit)
    return note


async def _sent_message(interaction: Any) -> Any | None:
    """Return the message an interaction just sent (None on failure)."""
    try:
        return await interaction.original_response()
    except Exception:
        return getattr(getattr(interaction, "response", None), "message", None)


async def _market_lord_select_view(
    wiring: KingdomsPanelWiring,
    king: Any,
    strings: dict[str, Any],
    anchor: Any = None,
) -> discord.ui.View | None:
    """Build the ephemeral lord select of the Explorateur (None if empty).

    marche-v2 art. 6: any ACTIVE lord of the kingdom may explore (not
    only the King) — the buyer picks the exploring lord here.
    """
    if wiring.kingdoms_service is None:
        return None
    kingdom_id = king.kingdom_id or ""
    try:
        lords = await wiring.kingdoms_service.lords()
    except Exception:
        logger.warning("KINGDOMS MARKET: lord list read failed", exc_info=True)
        return None
    candidates = [
        lord
        for lord in lords
        if lord.kingdom_id == kingdom_id and not getattr(lord, "left", False)
    ]
    options = [
        discord.SelectOption(
            label=str(getattr(lord, "display_name", lord.id))[:100],
            value=str(lord.id)[:100],
        )
        for lord in candidates
    ][:25]
    if not options:
        return None
    select: discord.ui.Select[Any] = discord.ui.Select(
        custom_id="kingdoms:market:choose:explorateur",
        placeholder=str(strings.get("market_choose_explorer") or strings["market_choose_lord"])[:100],
        options=options,
    )

    async def on_choose(target: discord.Interaction[Any]) -> None:
        values = getattr(target, "data", None) or {}
        chosen = [str(v) for v in values.get("values", [])]
        if not chosen:
            return
        economy = wiring.economy_service
        if economy is None:
            await target.response.edit_message(
                content=strings["market_no_service"], view=None
            )
            return
        try:
            lord = await economy.buy_explorateur(kingdom_id, chosen[0])
        except Exception as exc:
            note = await _market_failure_note(
                exc, wiring, king, "explorateur",
                int(getattr(_market_costs(wiring), "explorateur", 0) or 0), strings,
            )
            await target.response.edit_message(content=note, view=None)
            return
        note = await _market_success_note(
            wiring, kingdom_id, "explorateur", strings,
            str(getattr(lord, "display_name", lord.id)),
        )
        await _record_clicked_chronicle(
            wiring, str(target.user.id), "market_purchase",
            item=_market_label("explorateur", strings),
            cost=str(int(getattr(_market_costs(wiring), "explorateur", 0) or 0)),
        )
        await target.response.edit_message(content=note, view=None)
        _market_ttl(anchor or target, getattr(target, "message", None))
        await _refresh_realm_views_for_kingdom_safe(target.guild, kingdom_id)

    select.callback = on_choose  # type: ignore[method-assign, assignment]
    view = discord.ui.View(timeout=600)
    view.add_item(select)
    return view


async def _market_action_select_view(
    wiring: KingdomsPanelWiring,
    king: Any,
    action: str,
    strings: dict[str, Any],
    anchor: Any = None,
) -> discord.ui.View | None:
    """Build the ephemeral territory select of one special action (None when empty).

    remparts-v2: only the Corruption still walks this flow — the Garde
    Royale is a TOKEN bought here and PLACED from the Remparts salon.
    """
    if wiring.territories_service is None:
        return None
    kingdom_id = king.kingdom_id or ""
    territories = await wiring.territories_service.territories()
    candidates = [t for t in territories if t.owner_kingdom_id != kingdom_id]
    options: list[discord.SelectOption] = [
        discord.SelectOption(
            label=strings["market_territory_line"].format(t.map_key, t.owner_kingdom_id)[:100],
            value=t.id[:100],
        )
        for t in candidates
    ][:25]
    if not options:
        return None
    select: discord.ui.Select[Any] = discord.ui.Select(
        custom_id=f"kingdoms:market:choose:{action}",
        placeholder=strings["market_action"][action][:100],
        options=options,
    )

    async def on_choose(target: discord.Interaction[Any]) -> None:
        values = getattr(target, "data", None) or {}
        chosen = [str(v) for v in values.get("values", [])]
        if not chosen:
            return
        economy = wiring.economy_service
        if economy is None:
            await target.response.edit_message(
                content=strings["market_no_service"], view=None
            )
            return
        try:
            territory = await economy.buy_corruption(kingdom_id, chosen[0])
        except Exception as exc:
            cost = int(getattr(_market_costs(wiring), action, 0) or 0)
            note = await _market_failure_note(exc, wiring, king, action, cost, strings)
            await target.response.edit_message(content=note, view=None)
            return
        detail = strings["market_territory_line"].format(
            territory.map_key, territory.owner_kingdom_id
        )
        note = await _market_success_note(wiring, kingdom_id, action, strings, detail)
        await _record_clicked_chronicle(
            wiring, str(target.user.id), "market_purchase",
            item=_market_label(action, strings),
            cost=str(int(getattr(_market_costs(wiring), action, 0) or 0)),
        )
        await target.response.edit_message(content=note, view=None)
        _market_ttl(anchor or target, getattr(target, "message", None))
        await _refresh_realm_views_for_kingdom_safe(target.guild, kingdom_id)

    select.callback = on_choose  # type: ignore[method-assign, assignment]
    view = discord.ui.View(timeout=600)
    view.add_item(select)
    return view


async def _market_marry_lord_view(
    wiring: KingdomsPanelWiring,
    king: Any,
    strings: dict[str, Any],
) -> discord.ui.View | None:
    """Build the ephemeral lord select of the arranged marriage (D60/D74)."""
    if wiring.kingdoms_service is None:
        return None
    kingdom_id = king.kingdom_id or ""
    lords = [
        lord
        for lord in await wiring.kingdoms_service.lords()
        if getattr(lord, "kingdom_id", None) == kingdom_id
        and not getattr(lord, "left", False)
        and getattr(lord, "married_civilization", None) is None
    ][:25]
    if not lords:
        return None
    select: discord.ui.Select[Any] = discord.ui.Select(
        custom_id="kingdoms:market:choose:mariage_arrange",
        placeholder=strings["market_action"]["mariage_arrange"][:100],
        options=[
            discord.SelectOption(
                label=str(getattr(lord, "display_name", lord.id))[:100],
                value=str(lord.id)[:100],
            )
            for lord in lords
        ],
    )

    async def on_choose(target: discord.Interaction[Any]) -> None:
        values = getattr(target, "data", None) or {}
        chosen = [str(v) for v in values.get("values", [])]
        if not chosen:
            return
        await target.response.defer(ephemeral=True)
        view = await _market_marry_civ_view(wiring, king, chosen[0], strings)
        if view is None:
            await target.followup.send(strings["market_no_options"], ephemeral=True)
            return
        await target.followup.send(
            strings["market_choose_civ"], view=view, ephemeral=True
        )

    select.callback = on_choose  # type: ignore[method-assign, assignment]
    view = discord.ui.View(timeout=600)
    view.add_item(select)
    return view


async def _strip_mercenary_holders_safe(bot: Any, civ_key: str, strings: dict[str, Any]) -> None:
    """Strip + refund the mercenary holders of a marriage (T2c, best effort).

    chef-mercenaire-v1 (Drasah 24/10): a civilization acquired by
    MARRIAGE strips every holder of his mercenary copy and refunds him
    a FIXED 1 écu — each returned (lord, civ) becomes the « Remboursement
    de 1 écu » DM.
    """
    wiring = _wiring()
    service = getattr(wiring, "mercenary_service", None)
    if service is None or not civ_key:
        return
    try:
        stripped = await service.resolve_marriage_acquired(str(civ_key))
    except Exception:
        logger.warning("KINGDOMS: mercenary marriage strip failed", exc_info=True)
        return
    refund = int(
        getattr(getattr(service, "settings", None), "marriage_refund_ecus", 1) or 1
    )
    for lord, civ in stripped:
        try:
            user = bot.get_user(int(lord.id))
            if user is None:
                user = await bot.fetch_user(int(lord.id))
            if user is not None:
                await user.send(
                    strings["mercenary_refund_dm"].format(str(civ), refund)
                )
        except Exception:
            logger.warning(
                "KINGDOMS: mercenary refund DM to %s failed", lord.id, exc_info=True
            )


async def _market_marry_civ_view(
    wiring: KingdomsPanelWiring,
    king: Any,
    lord_id: str,
    strings: dict[str, Any],
) -> discord.ui.View | None:
    """Build the ephemeral civilization select (D74: any civ, exclusive)."""
    if wiring.diplomacy_service is None or wiring.economy_service is None:
        return None
    if wiring.kingdoms_service is None:
        return None
    married = {
        str(lord.married_civilization)
        for lord in await wiring.kingdoms_service.lords()
        if getattr(lord, "married_civilization", None) is not None
    }
    catalog = getattr(getattr(wiring.kingdoms_service, "config", None), "civilizations", ()) or ()
    options = [
        discord.SelectOption(
            label=str(getattr(civ, "display_name", civ.key))[:100],
            value=str(civ.key)[:100],
        )
        for civ in catalog
        if str(civ.key) not in married
    ][:25]
    if not options:
        return None
    select: discord.ui.Select[Any] = discord.ui.Select(
        custom_id=f"kingdoms:market:marryciv:{str(lord_id)[:80]}",
        placeholder=strings["market_action"]["mariage_arrange"][:100],
        options=options,
    )
    economy = wiring.economy_service
    diplomacy = wiring.diplomacy_service
    kingdom_id = king.kingdom_id or ""
    civ_names = {
        str(civ.key): str(getattr(civ, "display_name", civ.key)) for civ in catalog
    }

    async def on_choose(target: discord.Interaction[Any]) -> None:
        values = getattr(target, "data", None) or {}
        chosen = [str(v) for v in values.get("values", [])]
        if not chosen:
            return
        await target.response.defer(ephemeral=True)
        try:
            # D60/D74: consumes ONE token of the kingdom's stock — the
            # token was bought at the Marché; no payment happens here.
            # A ROYAL arranged marriage (the King weds himself) consumes
            # TWO tokens (Drasah's Reine rule, 2026-10-11).
            await diplomacy.arranged_marriage(lord_id, chosen[0])
        except Exception as exc:
            await target.followup.send(_market_error_note(exc, strings), ephemeral=True)
            return
        await _assign_marriage_lock_role_safe(target.guild, str(lord_id))
        # chef-mercenaire-v1 T2c: the sealed civ strips its mercenary
        # holders (fixed 1-écu refund DM).
        await _strip_mercenary_holders_safe(target.client, chosen[0], strings)
        try:
            wallet = await economy.wallet(kingdom_id)
        except Exception:
            wallet = 0
        royal = False
        hours = 6
        civ_label = civ_names.get(chosen[0], chosen[0])
        try:
            target_lord = next(
                (
                    item
                    for item in await wiring.kingdoms_service.lords()
                    if str(item.id) == str(lord_id)
                ),
                None,
            )
            royal = target_lord is not None and str(getattr(target_lord, "role", "")) == "king"
        except Exception:
            royal = False
        if royal:
            hours = 12
            kingdom_doc = await _kingdom_by_id_safe(wiring.kingdoms_service, kingdom_id)
            await target.followup.send(
                strings["church_marry_royal_arranged_ok"].format(civ_label, hours),
                ephemeral=True,
            )
            await _announce_royal_wedding_safe(
                target.guild,
                strings,
                str(getattr(king, "display_name", str(king.id))),
                str(getattr(kingdom_doc, "name", kingdom_id)),
                civ_label,
                hours,
                king_id=str(lord_id),
                lock_until=int(getattr(target_lord, "marriage_locked_until", 0) or 0),
            )
        else:
            await target.followup.send(
                strings["market_mariage_ok"].format(civ_label, wallet),
                ephemeral=True,
            )
        await _refresh_realm_views_for_kingdom_safe(target.guild, kingdom_id)

    select.callback = on_choose  # type: ignore[method-assign, assignment]
    view = discord.ui.View(timeout=600)
    view.add_item(select)
    return view


async def _market_next_patrouille_slot(
    wiring: KingdomsPanelWiring, kingdom_id: str
) -> int:
    """Return the next patrol slot to unlock (1 first, then 2)."""
    state = None
    if wiring.attacks_service is not None:
        try:
            state = await wiring.attacks_service.technology_state(kingdom_id)
        except Exception:
            state = None
    return 1 if not _patrouille_unlocked(state) else 2


async def _market_buy_note(
    interaction: discord.Interaction,
    note: str,
    kingdom_id: str,
    refresh: bool = True,
) -> None:
    """The single post-purchase ephemeral echo (+ realm refresh)."""
    message: Any = cast(Any, await interaction.followup.send(note, ephemeral=True))
    _market_ttl(interaction, message)
    if refresh:
        await _refresh_realm_views_for_kingdom_safe(interaction.guild, kingdom_id)


async def _market_purchase_flow(
    interaction: discord.Interaction,
    wiring: KingdomsPanelWiring,
    king: Any,
    kind: str,
    item: str,
    cost: int,
    strings: dict[str, Any],
) -> None:
    """cartes-tcg-v2: the shared purchase path of the article card
    ✅ button and the legacy modal sheet (marche-v2) — corruption and
    explorateur open their lord/target select views first."""
    economy = wiring.economy_service
    if economy is None:
        await interaction.response.send_message(strings["market_no_service"], ephemeral=True)
        return
    kingdom_id = king.kingdom_id or ""
    if item == "corruption":
        view = await _market_action_select_view(wiring, king, item, strings, anchor=interaction)
        if view is None:
            await interaction.response.send_message(strings["market_no_options"], ephemeral=True)
            return
        await interaction.response.send_message(
            strings["market_choose_corrupt"], view=view, ephemeral=True
        )
        return
    if item == "explorateur":
        # marche-v2 art. 6: the exploring lord is chosen AFTER the
        # sheet — 48h lock, the map comes at the end.
        view = await _market_lord_select_view(wiring, king, strings, anchor=interaction)
        if view is None:
            await interaction.response.send_message(strings["market_no_options"], ephemeral=True)
            return
        await interaction.response.send_message(
            str(strings.get("market_choose_explorer") or strings["market_choose_lord"]),
            view=view,
            ephemeral=True,
        )
        return
    await interaction.response.defer(ephemeral=True)
    try:
        if kind == "tech":
            await economy.buy_combat_technology(kingdom_id, item)
            note = await _market_success_note(wiring, kingdom_id, item, strings)
        elif item == "garde_royale":
            # remparts-v2: the market sells the TOKEN — the King
            # places it from the 🧱 Remparts salon.
            stock = await economy.buy_royal_guard(kingdom_id)
            note = str(strings["market_garde_token_ok"]).format(stock)
        elif item == "patrouille":
            # remparts-v2: the market sells the slot UNLOCK (slot 1
            # first, then slot 2) — the King places the window from
            # the 🧱 Remparts salon.
            slot_no = await _market_next_patrouille_slot(wiring, kingdom_id)
            await economy.buy_patrouille_slot(kingdom_id, slot_no)
            note = str(strings["market_patrouille_slot_ok"]).format(slot_no)
        else:  # mariage_arrange: the token, not the marriage (D60).
            kingdom = await economy.buy_arranged_marriage_token(kingdom_id)
            note = str(strings["market_arranged_token_ok"]).format(
                int(getattr(kingdom, "arranged_marriage_stock", 0) or 0)
            )
    except Exception as exc:
        note = await _market_failure_note(exc, wiring, king, item, cost, strings)
        await _market_buy_note(interaction, note, kingdom_id, refresh=False)
        return
    await _record_clicked_chronicle(
        wiring, str(interaction.user.id), "market_purchase",
        item=_market_label(item, strings),
        cost=str(int(cost or 0)),
    )
    await _market_buy_note(interaction, note, kingdom_id)


def _market_card_file(item: str) -> discord.File | None:
    """The TCG card of one article, when it was uploaded (cosmetic)."""
    from kingdoms.mods.kingdoms.marche_art import card_filename, resolve_card

    path = resolve_card(item)
    if path is None:
        logger.info("KINGDOMS MARKET: %r card missing (assets/marche)", item)
        return None
    return discord.File(path, filename=card_filename(item))


_EPHEMERAL_WINDOW_TTL = 15 * 60
"""fenetre-unique-v1 (Drasah 26/10): a card's interaction webhook token
lives ~15 minutes — past that, the old ephemeral can no longer be
deleted (Discord keeps it on the player's side until dismissed)."""

_EPHEMERAL_WINDOWS: dict[str, tuple[discord.Interaction, float]] = {}
"""One live ephemeral window per player (user id -> (interaction, opened_at)).
fenetre-unique-v1 (Drasah 26/10): clicking another article CLOSES the
previous card instead of stacking ephemerals. fenetre-unique-v2
(Drasah 27/10): the registry is shared by EVERY ephemeral reveal of the
mod (revealed market card, role rights sheet, ...) — his 27/10 live
report: the Roi and Seigneur sheets stacked below the panel. Bounded:
one entry max per player, replaced on every reveal — no growth, no
cleanup pass needed."""


async def _close_previous_ephemeral(user_id: str) -> None:
    """Best-effort: delete the player's previous ephemeral window."""
    previous = _EPHEMERAL_WINDOWS.pop(user_id, None)
    if previous is None:
        return
    old_interaction, opened_at = previous
    if time.monotonic() - opened_at > _EPHEMERAL_WINDOW_TTL:
        return  # token expiré — Discord laisse l'éphémère mourir seule
    try:
        await old_interaction.delete_original_response()
        logger.info("KINGDOMS: previous ephemeral window closed for %s", user_id)
    except Exception:
        logger.info(
            "KINGDOMS: previous ephemeral window close failed (ignored)",
            exc_info=True,
        )


async def _open_unique_ephemeral(
    interaction: discord.Interaction, content: str | None = None, **kwargs: Any
) -> None:
    """Open THE player's single ephemeral window (shared registry).

    fenetre-unique-v3 (Drasah 28/10): every flow ENTRY of the
    attack panel routes here — opening a window closes the
    player's previous one (his 28/10 live report: the « Choisis
    le royaume » view stacked twice under the declare panel on
    repeated clicks).
    """
    user_id = str(getattr(interaction.user, "id", ""))
    await _close_previous_ephemeral(user_id)
    await interaction.response.send_message(content, ephemeral=True, **kwargs)
    _EPHEMERAL_WINDOWS[user_id] = (interaction, time.monotonic())


async def _send_market_card(
    interaction: discord.Interaction,
    wiring: KingdomsPanelWiring,
    king: Any,
    kind: str,
    item: str,
    strings: dict[str, Any],
) -> None:
    """cartes-tcg-v2 (Drasah 26/10): reveal the article CARD face up
    in an ephemeral — the TCG art, the full fiche (effect, cost, live
    treasury, per-season remainder) and the ✅ Acheter / ❌ Annuler
    buttons. No art uploaded? The sheet still works (cosmetic)."""
    logger.info("KINGDOMS MARKET TRACE: card build START kind=%r item=%r", kind, item)
    cost = int(getattr(_market_costs(wiring), item, 0) or 0)
    logger.info("KINGDOMS MARKET TRACE: card cost=%s -> fiche", cost)
    fiche = await _market_fiche(wiring, king, item, strings)
    logger.info("KINGDOMS MARKET TRACE: card fiche built (%d chars) -> embed/view", len(fiche))
    embed = discord.Embed(
        title=f"💰 {_market_label(item, strings)} — {cost} ⚙️",
        description=fiche,
        colour=discord.Colour.gold(),
    )
    file = _market_card_file(item)
    if file is not None:
        embed.set_image(url=f"attachment://{file.filename}")
    view = discord.ui.View(timeout=None)
    view.add_item(KingdomMarketConfirmButton(kind, item, strings))
    view.add_item(KingdomMarketCancelButton(strings))
    logger.info("KINGDOMS MARKET TRACE: card READY item=%r file=%s -> send", item, file is not None)
    # fenetre-unique-v1 (Drasah 26/10): one revealed card at a time —
    # opening a new article closes the previous ephemeral (his 26/10
    # live report: stacked Contre-Espionnage + Jeu d'armes windows).
    user_id = str(getattr(interaction.user, "id", ""))
    await _close_previous_ephemeral(user_id)
    await interaction.response.send_message(
        embed=embed,
        files=[file] if file is not None else [],
        view=view,
        ephemeral=True,
    )
    _EPHEMERAL_WINDOWS[user_id] = (interaction, time.monotonic())
    logger.info("KINGDOMS MARKET TRACE: card SENT item=%r", item)


class KingdomMarketConfirmButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=(
        r"kingdoms:market:confirm:(?P<kind>tech|action):(?P<item>embuscade"
        r"|traquenard|contre_espionnage|sabotage|jeu_d_armes|explorateur"
        r"|corruption|garde_royale|patrouille|mariage_arrange)"
    ),
):
    """cartes-tcg-v2: the restart-proof ✅ of the revealed article card."""

    def __init__(self, kind: str, item: str, strings: dict[str, Any]) -> None:
        super().__init__(
            discord.ui.Button(
                label=str(strings["market_buy_button"])[:80],
                custom_id=f"kingdoms:market:confirm:{kind}:{item}",
                style=discord.ButtonStyle.success,
            )
        )
        self.kind = kind
        # cartes-tcg-v2 incident (Drasah 26/10): ``DynamicItem.item`` IS
        # the wrapped Button discord.py reads ``_total_count`` from —
        # storing the article key there crashed every card reveal with
        # ``AttributeError: 'str' object has no attribute '_total_count'``
        # before the ephemeral could be answered ("n'a pas répondu à
        # temps"). The article key rides ``self.article``.
        self.article = item

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomMarketConfirmButton:
        """Rebuild the card confirm button from the wire."""
        locale = str(interaction.locale) if interaction.locale else "en"
        logger.info("KINGDOMS MARKET TRACE: rebuild CONFIRM %r/%r from the wire", match.group("kind"), match.group("item"))
        return cls(match.group("kind"), match.group("item"), _strings(locale))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Run the purchase the card announced (single ephemeral echo)."""
        locale = str(interaction.locale) if interaction.locale else "en"
        strings = _strings(locale)
        wiring = _wiring()
        if wiring.economy_service is None:
            await interaction.response.send_message(strings["market_no_service"], ephemeral=True)
            return
        king = await _king_of(wiring.kingdoms_service, str(interaction.user.id))
        if king is None:
            await interaction.response.send_message(strings["market_not_king"], ephemeral=True)
            return
        cost = int(getattr(_market_costs(wiring), self.article, 0) or 0)
        await _market_purchase_flow(
            interaction, wiring, king, self.kind, self.article, cost, strings
        )


class KingdomMarketCancelButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:market:cancel",
):
    """cartes-tcg-v2: the ❌ of the revealed card — face down again."""

    def __init__(self, strings: dict[str, Any]) -> None:
        super().__init__(
            discord.ui.Button(
                label=str(strings["market_cancel_button"])[:80],
                custom_id="kingdoms:market:cancel",
                style=discord.ButtonStyle.danger,
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomMarketCancelButton:
        """Rebuild the card cancel button from the wire."""
        locale = str(interaction.locale) if interaction.locale else "en"
        logger.info("KINGDOMS MARKET TRACE: rebuild CANCEL from the wire")
        return cls(_strings(locale))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Close the revealed card: the ephemeral window disappears.

        cartes-tcg-v2 retour live (Drasah 26/10): the ❌ should just
        CLOSE the ephemeral — a leftover « Achat annulé » message was
        noise. The card message IS the interaction's original response,
        so defer + delete_original_response makes the window vanish.
        Deletion is best-effort: past the 15-minute window (or on a
        mock), fold the card back to the plain cancelled note instead.
        """
        locale = str(interaction.locale) if interaction.locale else "en"
        strings = _strings(locale)
        try:
            await interaction.response.defer()
            await interaction.delete_original_response()
            logger.info("KINGDOMS MARKET: card window closed (deleted ephemeral)")
        except Exception:
            logger.warning(
                "KINGDOMS MARKET: card window deletion failed — folding back",
                exc_info=True,
            )
            try:
                await interaction.response.edit_message(
                    content=strings["market_cancelled"], embed=None, view=None
                )
            except Exception:
                logger.warning(
                    "KINGDOMS MARKET: card fold-back failed too", exc_info=True
                )


class KingdomMarketModal(discord.ui.Modal):
    """The marche-v2 article sheet (variant B): read the fiche, then buy.

    The fiche rides a prefilled read-only paragraph input — the player
    sees the effect, the cost, the live treasury and the per-season
    remainder BEFORE the single purchase click (Discord's submit button
    cannot be relabelled, so the cost rides the modal title).
    """

    fiche: discord.ui.TextInput[KingdomMarketModal] = discord.ui.TextInput(
        label="Sheet",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=4000,
    )

    def __init__(
        self,
        *,
        kind: str,
        item: str,
        cost: int,
        fiche: str,
        strings: dict[str, Any],
        wiring: KingdomsPanelWiring,
        king: Any,
    ) -> None:
        label = _market_label(item, strings)
