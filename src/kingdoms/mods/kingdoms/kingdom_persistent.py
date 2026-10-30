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
    "KingdomMarketUseButton",
    "KingdomMarketConfirmButton",
    "KingdomMarketCardInfoButton",
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
        KingdomDisputeChoiceSelect,
        KingdomDisputeConfirmButton,
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
    from kingdoms.mods.kingdoms.kingdom_immersion import (
        KingdomOutgameHideButton,
        KingdomOutgameShowButton,
        KingdomOutgameToggle,
        KingdomUserSettingsButton,
    )
    from kingdoms.mods.kingdoms.kingdom_time_panel import KingdomTimeButton
    from kingdoms.mods.kingdoms.kingdom_voliere_panel import (
        KingdomTheftButton,
        KingdomVoliereButton,
    )
    # exploration-expedition-v1 (Drasah 30/10): the 🧭 expedition salon
    # — the realm/chamber open button and the three boarding buttons.
    from kingdoms.mods.kingdoms.kingdom_expedition_panel import (
        KingdomExpeditionChefButton,
        KingdomExpeditionCrewButton,
        KingdomExpeditionLaunchButton,
        KingdomExpeditionOpenButton,
    )

    bot.add_dynamic_items(
        KingdomExpeditionOpenButton,
        KingdomExpeditionChefButton,
        KingdomExpeditionCrewButton,
        KingdomExpeditionLaunchButton,
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
        # litige-mp-v1 (Drasah 30/10): the dispute DM round's two-kingdom
        # select + confirm button (restart-safe, player DMs).
        KingdomDisputeChoiceSelect,
        KingdomDisputeConfirmButton,
        KingdomApplyButton,
        KingdomCandidatureButton,
        KingdomProfileButton,
        KingdomRequestButton,
        KingdomAdminButton,
        KingdomMarketTechButton,
        KingdomMarketActionButton,
        KingdomMarketLedgerButton,
        KingdomMarketCardInfoButton,
        KingdomMarketConfirmButton,
        KingdomMarketCancelButton,
        KingdomMarketUseButton,
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
        # reglages-interrupteur-v1 (Drasah 30/10): the settings switch,
        # its opener, plus the OLD show/hide pair kept registered for
        # the ≤15-minute living ephemerals (compat, never rebuilt later).
        KingdomOutgameToggle,
        KingdomUserSettingsButton,
        KingdomOutgameShowButton,
        KingdomOutgameHideButton,
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
        # fenetre-unique-globale-v1 (Drasah 30/10): joins the shared
        # single-window registry — no stacking with the player's
        # previous ephemeral view.
        await _open_unique_ephemeral(
            interaction,
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


async def _candidature_kingdom_approved(kingdoms_service: Any, kingdom_name: str | None) -> bool:
    """vision-gating-v1 (Drasah 30/10): the game roles ride an APPROVED kingdom.

    Live report: a King validated mid-flow saw the game salons the
    moment he CREATED his kingdom — before any admin accepted it (and
    a queued player has no kingdom at all). The Discord role is now
    granted ONLY when the player's kingdom is approved; the kingdom
    validation, the throne claim and the admin assignment are the
    surfaces that grant it.
    """
    if kingdoms_service is None or not kingdom_name:
        return False
    try:
        kingdoms = await kingdoms_service.kingdoms()
    except Exception:
        logger.warning("CANDIDATURES: kingdom approval lookup failed", exc_info=True)
        return False
    wanted = str(kingdom_name).strip().casefold()
    for kingdom in kingdoms:
        if str(getattr(kingdom, "name", "")).strip().casefold() == wanted:
            return str(getattr(kingdom, "validation", "")) == "approved"
    return False


async def _grant_player_mod_role_safe(
    wiring: KingdomsPanelWiring, guild: discord.Guild | None, member_id: str, role_key: str
) -> bool:
    """Grant one kingdoms player role through the ModRolesService (best effort)."""
    if wiring.mod_roles_service is None or guild is None or not str(member_id).isdigit():
        return False
    from kingdoms.mods.kingdoms.kingdom_panels import KINGDOM_MOD

    try:
        await wiring.mod_roles_service.assign_mod_role(str(guild.id), str(member_id), KINGDOM_MOD, role_key)
        return True
    except Exception:
        logger.warning("CANDIDATURES: role assignment failed", exc_info=True)
        return False


async def _log_admin_action_safe(guild: discord.Guild | None, content: str) -> None:
    """admin-logs-v1 (Drasah 30/10): the destructive admin actions land in
    the 📜 journal-admin salon, stamped in Paris time. Best effort: a
    missing salon (or a failed send) NEVER breaks the action itself."""
    if guild is None:
        return
    try:
        from kingdoms.mods.kingdoms.kingdom_panels import NO_MENTIONS
        from kingdoms.mods.kingdoms.kingdom_setup import _slug

        target = _slug("journal-admin")
        channel = next(
            (
                channel
                for channel in getattr(guild, "text_channels", [])
                if _slug(str(getattr(channel, "name", ""))) == target
            ),
            None,
        )
        if channel is None:
            return
        stamp = datetime.now(ZoneInfo("Europe/Paris")).strftime("%H:%M %d/%m")
        await channel.send(f"[{stamp} Paris] {content}", allowed_mentions=NO_MENTIONS)
    except Exception:
        logger.warning("KINGDOMS: admin log write failed", exc_info=True)


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
    from kingdoms.mods.kingdoms.kingdom_panels import ROLE_KING, ROLE_LORD

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
    # vision-gating-v1 (Drasah 30/10): the game role is granted ONLY
    # when the player's kingdom is ACCEPTED — a King founding his
    # kingdom (or a queued player) stays out-of-game until the kingdom
    # is validated, claimed or assigned.
    if applicant_id and await _candidature_kingdom_approved(wiring.kingdoms_service, kingdom_name):
        if await _grant_player_mod_role_safe(wiring, guild, applicant_id, role_key):
            extra += " " + strings["role_assigned"].format(role_key)
        else:
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
        if item == "jeu_d_armes":
            # chambre-utiliser-v1: the buys ride the bought counter —
            # the APPLIED AI levels stay under "jeu_d_armes".
            return int(purchases.get("jeu_d_armes_bought", 0) or 0)
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
    # marche-fiche-v1 (Drasah 30/10): étiquettes grassoulignées, effet
    # en citation (la barre grise « surligne »), « : » au lieu du trait
    # — et une ligne vide entre chaque rubrique pour aérer l'embed.
    return "\n\n".join(lines)


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
    chambre-utiliser-v1 (Drasah 30/10): the view is now the USE flow of
    the 🏛️ Chambre des comptes — the pick consumes a TOKEN (the payment
    happened at the Marché).
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
            territory = await economy.use_corruption(kingdom_id, chosen[0])
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
        await _append_purchase_receipt(target, note, strings, wiring, king)
        _market_ttl(anchor or target, getattr(target, "message", None))
        await _refresh_realm_views_for_kingdom_safe(target.guild, kingdom_id)
        # achat-fermeture-v1 (Drasah 30/10): same rule after the select
        # flows — the card window that opened the purchase closes.
        await _close_previous_ephemeral(str(target.user.id))

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


_RECEIPT_TTL = 15 * 60
"""achat-recu-v1 (Drasah 30/10): a followup webhook token lives ~15
minutes — past that the receipt window is DEAD (no edit possible) and a
fresh receipt opens instead of stacking bubbles."""

_RECEIPT_WINDOWS: dict[str, dict[str, Any]] = {}
"""One living cumulative purchase receipt per player (achat-recu-v1,
Drasah 30/10): the 2nd purchase adds its line to the SAME ephemeral
(edit in place) instead of stacking throwaway « ✅ Acheté » bubbles.
The receipt lives in its OWN registry slot — opening other market
cards (the fenêtre unique) NEVER closes it. Bounded: one entry max per
player, replaced when its window expires (the dead one dies alone)."""


def _receipt_body(title: str, lines: list[str], footer: str = "") -> str:
    """Render the receipt (title + lines + the once-only ledger footer),
    keeping the LATEST under 4000 chars — an expired-window receipt is
    naturally short anyway (no cap decided: the 15-minute edit window
    bounds the growth on its own). marche-retour-achats-v1 (Drasah
    30/10): the footer lives at the BOTTOM of the body, appended ONCE
    per receipt — never duplicated per purchase line."""
    while sum(len(x) for x in lines) > 3400 and len(lines) > 1:
        lines = lines[1:]
    body = f"{title}\n" + "\n".join(lines)
    if footer:
        body = f"{body}\n\n{footer}"
    return body


async def _realm_ledger_footer(
    guild: Any, wiring: KingdomsPanelWiring | None, king: Any, strings: dict[str, Any]
) -> str:
    """The receipt's « where are my purchases » footer.

    marche-retour-achats-v1 (Drasah 30/10): points the player to the 🏛️
    Chambre des comptes — the CLICKABLE mention of HIS kingdom's
    « le-royaume » salon. Best effort: no kingdom / no salon / no
    permission → the static fallback text; a crash → empty (the receipt
    itself never breaks)."""
    named = str(strings.get("market_receipt_ledger_help_named") or "")
    fallback = str(strings.get("market_receipt_ledger_help") or "")
    if not named and not fallback:
        return ""
    try:
        kingdom_id = str(getattr(king, "kingdom_id", "") or "")
        service = getattr(wiring, "kingdoms_service", None) if wiring else None
        kingdom = await _kingdom_by_id_safe(service, kingdom_id)
        if kingdom is not None and guild is not None:
            from kingdoms.mods.kingdoms.kingdom_realms import _find_realm_category
            from kingdoms.mods.kingdoms.kingdom_setup import _slug

            category = _find_realm_category(guild, str(getattr(kingdom, "name", "")))
            channel = next(
                (
                    c
                    for c in getattr(category, "channels", [])
                    if _slug(getattr(c, "name", "")) == "le-royaume"
                ),
                None,
            )
            if channel is not None:
                return named.format(mention=f"<#{channel.id}>")
    except Exception:
        logger.warning("KINGDOMS MARKET: receipt ledger lookup failed", exc_info=True)
    return fallback


async def _append_purchase_receipt(
    interaction: discord.Interaction,
    line: str,
    strings: dict[str, Any],
    wiring: KingdomsPanelWiring | None = None,
    king: Any = None,
) -> None:
    """Add one ✅ purchase line to the player's cumulative receipt.

    achat-recu-v1 (Drasah 30/10): the receipt is the living trace of the
    session's purchases — an edit in place while the window lives, a
    FRESH receipt once the ~15-minute webhook token is dead. Best
    effort: a failed edit/send NEVER breaks the purchase (it already
    happened); the line just falls back to a throwaway send."""
    user_id = str(getattr(interaction.user, "id", ""))
    if not user_id:
        return
    title = str(strings["market_receipt_title"])
    receipt = _RECEIPT_WINDOWS.get(user_id)
    if receipt is not None and time.monotonic() - receipt["opened_at"] <= _RECEIPT_TTL:
        receipt["lines"].append(line)
        try:
            await receipt["message"].edit(
                content=_receipt_body(title, receipt["lines"], receipt.get("footer", ""))
            )
            return
        except Exception:
            logger.warning(
                "KINGDOMS MARKET: receipt edit failed — fresh receipt", exc_info=True
            )
            _RECEIPT_WINDOWS.pop(user_id, None)
    lines = [line]
    # marche-retour-achats-v1: the footer is resolved ONCE per receipt
    # window (the mention cannot change during a 15-minute session).
    footer = await _realm_ledger_footer(interaction.guild, wiring, king, strings)
    try:
        message = await interaction.followup.send(
            _receipt_body(title, lines, footer), ephemeral=True
        )
    except Exception:
        logger.warning(
            "KINGDOMS MARKET: receipt send failed (purchase stands)", exc_info=True
        )
        return
    _RECEIPT_WINDOWS[user_id] = {
        "opened_at": time.monotonic(),
        "message": message,
        "lines": lines,
        "footer": footer,
    }


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
    ✅ button and the legacy modal sheet (marche-v2). chambre-utiliser-v1
    (Drasah 30/10): the corruption / jeu d'armes / explorateur are
    TOKEN buys — the effect fires at the 🚀 Utiliser of the Chambre
    des comptes, never here."""
    economy = wiring.economy_service
    if economy is None:
        await interaction.response.send_message(strings["market_no_service"], ephemeral=True)
        return
    kingdom_id = king.kingdom_id or ""
    await interaction.response.defer(ephemeral=True)
    try:
        # chambre-utiliser-v1 (Drasah 30/10): the Marché SELLS TOKENS for
        # the corruption / jeu d'armes / explorateur — the EFFECT fires
        # at the 🚀 Utiliser of the 🏛️ Chambre des comptes, never here.
        if item == "corruption":
            stock = await economy.buy_corruption_token(kingdom_id)
            note = str(strings["market_corruption_token_ok"]).format(stock)
        elif item == "explorateur":
            stock = await economy.buy_explorateur_token(kingdom_id)
            note = str(strings["market_explorateur_token_ok"]).format(stock)
        elif item == "jeu_d_armes":
            stock = await economy.buy_jeu_d_armes_token(kingdom_id)
            note = str(strings["market_jeu_d_armes_token_ok"]).format(stock)
        elif kind == "tech":
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
    await _append_purchase_receipt(interaction, note, strings, wiring, king)
    await _refresh_realm_views_for_kingdom_safe(interaction.guild, kingdom_id)
    # achat-fermeture-v1 (Drasah 30/10): the purchase is DONE — the card
    # window closes by itself (the receipt stays as the living trace).
    # Best effort: a >15-min-old card has a dead token and dies alone.
    await _close_previous_ephemeral(str(interaction.user.id))


def _market_card_file(item: str) -> discord.File | None:
    """The TCG card of one article, when it was uploaded (cosmetic)."""
    from kingdoms.mods.kingdoms.marche_art import card_filename, resolve_card

    path = resolve_card(item)
    if path is None:
        logger.info("KINGDOMS MARKET: %r card missing (assets/marche)", item)
        return None
    return discord.File(path, filename=card_filename(item))


def _market_card_back_file() -> discord.File | None:
    """The TCG card BACK, when uploaded (chambre-des-comptes-v6)."""
    from kingdoms.mods.kingdoms.marche_art import resolve_card_back

    path = resolve_card_back()
    if path is None:
        logger.info("KINGDOMS MARKET: card back missing (assets/marche)")
        return None
    return discord.File(path, filename="Dos_de_carte.png")


_EPHEMERAL_WINDOW_TTL = 15 * 60
"""fenetre-unique-v1 (Drasah 26/10): a card's interaction webhook token
lives ~15 minutes — past that, the old ephemeral can no longer be
deleted (Discord keeps it on the player's side until dismissed)."""

_EPHEMERAL_WINDOWS: dict[str, tuple[discord.Interaction, float, Any]] = {}
"""One live ephemeral window per player: (interaction, opened_at, message).

fenetre-unique-v3 (chambre-des-comptes-fix-v3, Drasah 29/10): windows
opened through defer+followup carry their FOLLOWUP MESSAGE as the third
slot — deleting them via ``delete_original_response`` would delete the
PANEL the component sits on (a type-6 deferred interaction's original
response IS the component's message — Drasah's live report: the
Le-Royaume panel vanished on a ledger click). Windows opened through
``response.send_message`` keep the legacy 2-tuple shape and the original
response path.
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
    old_interaction, opened_at = previous[0], previous[1]
    window = previous[2] if len(previous) > 2 else None
    if time.monotonic() - opened_at > _EPHEMERAL_WINDOW_TTL:
        return  # token expiré — Discord laisse l'éphémère mourir seule
    try:
        if window is not None:
            # A defer+followup window: delete the followup MESSAGE —
            # never ``delete_original_response`` (type-6 defer makes
            # the original response THE PANEL the button sits on).
            await window.delete()
        else:
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
        super().__init__(title=f"💰 {label} — {cost} ⚙️"[:45], timeout=300)
        self.kind = kind
        self.item = item
        self.cost = cost
        self.strings = strings
        self.wiring = wiring
        self.king = king
        self.fiche.label = str(strings["market_fiche_input"])[:45]
        self.fiche.default = fiche[:4000]

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Run the purchase the sheet announced (single ephemeral echo).

        cartes-tcg-v2: the card flow now owns this path — the modal
        stays wired for backward compatibility and delegates to the
        shared :func:`_market_purchase_flow`.
        """
        if self.wiring.economy_service is None:
            await interaction.response.send_message(
                self.strings["market_no_service"], ephemeral=True
            )
            return
        await _market_purchase_flow(
            interaction, self.wiring, self.king, self.kind, self.item,
            self.cost, self.strings,
        )


def _patrouille_unlocked(state: Any) -> bool:
    """Whether the patrol slot 1 is already unlocked (remparts-v2)."""
    if state is None:
        return False
    purchases = getattr(state, "purchases", None) or {}
    return "patrouille_unlock_1" in purchases


class KingdomMarketTechButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=(
        r"kingdoms:market:tech:(?P<tech>embuscade|traquenard"
        r"|contre_espionnage|sabotage|jeu_d_armes)"
    ),
):
    """The restart-proof buy button of one combat technology (Marché)."""

    def __init__(self, tech: str, label: str, style: discord.ButtonStyle) -> None:
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                custom_id=f"kingdoms:market:tech:{tech}",
                style=style,
            )
        )
        self.tech = tech

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomMarketTechButton:
        """Rebuild the technology button from the wire."""
        locale = str(interaction.locale) if interaction.locale else "en"
        strings = _strings(locale)
        tech = match.group("tech")
        logger.info("KINGDOMS MARKET TRACE: rebuild tech button %r from the wire", tech)
        label = f"{strings['market_tech'][tech]}"
        return cls(tech, label, discord.ButtonStyle.primary)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the article sheet modal (marche-v2: no more instant buys)."""
        logger.info("KINGDOMS MARKET TRACE: tech click %r ENTER (user=%s)", self.tech, getattr(interaction.user, "id", "?"))
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
        logger.info("KINGDOMS MARKET TRACE: tech click %r king found -> card", self.tech)
        await _send_market_card(interaction, wiring, king, "tech", self.tech, strings)


class KingdomMarketActionButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=(
        r"kingdoms:market:action:(?P<action>explorateur|corruption"
        r"|garde_royale|patrouille|mariage_arrange)"
    ),
):
    """The restart-proof button opening one special-action purchase flow."""

    def __init__(self, action: str, label: str, style: discord.ButtonStyle) -> None:
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                custom_id=f"kingdoms:market:action:{action}",
                style=style,
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
    ) -> KingdomMarketActionButton:
        """Rebuild the action button from the wire."""
        locale = str(interaction.locale) if interaction.locale else "en"
        strings = _strings(locale)
        action = match.group("action")
        logger.info("KINGDOMS MARKET TRACE: rebuild action button %r from the wire", action)
        return cls(action, strings["market_action"][action], discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the article sheet modal (marche-v2: sheet before purchase)."""
        logger.info("KINGDOMS MARKET TRACE: action click %r ENTER (user=%s)", self.action, getattr(interaction.user, "id", "?"))
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
        logger.info("KINGDOMS MARKET TRACE: action click %r king found -> card", self.action)
        await _send_market_card(interaction, wiring, king, "action", self.action, strings)


class KingdomRempartsOpenButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:remparts:open",
):
    """The restart-proof « 🧱 Remparts » ephemeral button (restructuration-serveur-v1).

    Drasah 19/10: the realm loses the Remparts SALON — the view (AI
    level, Patrouille 1/2, Gardes Royales + reserve, the three
    King-only placement buttons and the defense manager) opens as an
    EPHEMERAL view from the Le-Royaume panel.
    """

    def __init__(self, label: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                custom_id="kingdoms:remparts:open",
                style=discord.ButtonStyle.secondary,
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomRempartsOpenButton:
        """Rebuild from the wire (merged strings — leçon Check 30)."""
        locale = str(interaction.locale) if interaction.locale else "en"
        return cls(_strings(locale)["remparts_open_button"])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the ephemeral Remparts view of the clicker's kingdom."""
        from kingdoms.mods.kingdoms.kingdom_realm_content import _build_patrol, _gather_state

        locale = str(interaction.locale) if interaction.locale else "en"
        strings = _strings(locale)
        wiring = _wiring()
        if wiring.kingdoms_service is None:
            await interaction.response.send_message(strings["market_no_service"], ephemeral=True)
            return
        lord = await _lord_of(wiring.kingdoms_service, str(interaction.user.id))
        if lord is None or getattr(lord, "left", False):
            await interaction.response.send_message(strings["not_lord"], ephemeral=True)
            return
        kingdoms = await wiring.kingdoms_service.kingdoms()
        kingdom = next(
            (k for k in kingdoms if str(k.id) == str(getattr(lord, "kingdom_id", None))),
            None,
        )
        if kingdom is None:
            await interaction.response.send_message(strings["not_lord"], ephemeral=True)
            return
        state = await _gather_state(kingdom, wiring)
        # fenetre-unique-royaume-v1 (Drasah 30/10): the Le-Royaume open
        # buttons join the shared single-window registry — opening one
        # closes the player's previous ephemeral (his live report: the
        # Chambre des comptes stacked with Remparts/Seigneurie).
        await _open_unique_ephemeral(
            interaction, view=_build_patrol(strings, kingdom, state), ephemeral=True
        )


class KingdomSeigneurieOpenButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:seigneurie:open",
):
    """The restart-proof « 🛡️ Seigneurie » ephemeral button (restructuration-serveur-v1).

    Drasah 19/10: the realm loses the Seigneurs SALON — the roster of
    ONE'S OWN kingdom (role, marriage, budgets, public locks) opens as
    an EPHEMERAL view from the Le-Royaume panel. The Corbeau « suivi »
    is NEVER displayed (espionnage secret — règle Drasah 19/10).
    """

    def __init__(self, label: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                custom_id="kingdoms:seigneurie:open",
                style=discord.ButtonStyle.secondary,
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomSeigneurieOpenButton:
        """Rebuild from the wire (merged strings — leçon Check 30)."""
        locale = str(interaction.locale) if interaction.locale else "en"
        return cls(_strings(locale)["seigneurie_open_button"])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the ephemeral Seigneurie roster of the clicker's kingdom."""
        from kingdoms.mods.kingdoms.kingdom_realm_content import _build_roster, _gather_state

        locale = str(interaction.locale) if interaction.locale else "en"
        strings = _strings(locale)
        wiring = _wiring()
        if wiring.kingdoms_service is None:
            await interaction.response.send_message(strings["market_no_service"], ephemeral=True)
            return
        lord = await _lord_of(wiring.kingdoms_service, str(interaction.user.id))
        if lord is None or getattr(lord, "left", False):
            await interaction.response.send_message(strings["not_lord"], ephemeral=True)
            return
        kingdoms = await wiring.kingdoms_service.kingdoms()
        kingdom = next(
            (k for k in kingdoms if str(k.id) == str(getattr(lord, "kingdom_id", None))),
            None,
        )
        if kingdom is None:
            await interaction.response.send_message(strings["not_lord"], ephemeral=True)
            return
        state = await _gather_state(kingdom, wiring)
        # fenetre-unique-royaume-v1 (Drasah 30/10): same registry — one
        # live ephemeral window per player across the Le-Royaume panel.
        await _open_unique_ephemeral(
            interaction, view=_build_roster(strings, kingdom, state), ephemeral=True
        )


class KingdomRempartsGuardButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:remparts:guard",
):
    """The restart-proof 🛡️ « Placer un Garde Royal » button (remparts-v2).

    remparts-v2 (Drasah 14/10): the Marché sells guard TOKENS (several
    simultaneously, no hard limit); the King POSSES them here on the
    map of his choice — one guard per map, 12h each, non-retroactive.
    King only.
    """

    def __init__(self, strings: dict[str, Any]) -> None:
        super().__init__(
            discord.ui.Button(
                label=str(strings["remparts_guard_button"])[:80],
                custom_id="kingdoms:remparts:guard",
                style=discord.ButtonStyle.primary,
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomRempartsGuardButton:
        """Rebuild the guard button from the wire."""
        locale = str(interaction.locale) if interaction.locale else "en"
        return cls(_strings(locale))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the King-only owned-map select and place one guard token."""
        locale = str(interaction.locale) if interaction.locale else "en"
        strings = _strings(locale)
        wiring = _wiring()
        if wiring.economy_service is None or wiring.territories_service is None:
            await interaction.response.send_message(strings["market_no_service"], ephemeral=True)
            return
        king = await _king_of(wiring.kingdoms_service, str(interaction.user.id))
        if king is None:
            await interaction.response.send_message(strings["market_not_king"], ephemeral=True)
            return
        kingdom_id = king.kingdom_id or ""
        owned = [
            t
            for t in await wiring.territories_service.territories()
            if t.owner_kingdom_id == kingdom_id
        ]
        if not owned:
            await interaction.response.send_message(strings["market_no_options"], ephemeral=True)
            return
        select: discord.ui.Select[Any] = discord.ui.Select(
            custom_id="kingdoms:remparts:choose:guard",
            placeholder=str(strings["remparts_place_guard_choose"])[:100],
            options=[
                discord.SelectOption(
                    label=str(getattr(t, "map_key", t.id))[:100], value=str(t.id)[:100]
                )
                for t in owned[:25]
            ],
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
                territory = await economy.place_royal_guard(kingdom_id, chosen[0])
            except Exception as exc:
                await target.response.edit_message(
                    content=_market_error_note(exc, strings),
                    view=None,
                )
                _market_ttl(interaction, getattr(target, "message", None))
                return
            until = datetime.fromtimestamp(
                int(territory.protected_until.timestamp()), tz=ZoneInfo("Europe/Paris")
            )
            note = str(strings["remparts_guard_placed"]).format(
                str(getattr(territory, "map_key", chosen[0])),
                until.strftime("%d/%m %H:%M"),
            )
            await target.response.edit_message(content=note, view=None)
            _market_ttl(interaction, getattr(target, "message", None))
            await _refresh_realm_views_for_kingdom_safe(target.guild, kingdom_id)

        select.callback = on_choose  # type: ignore[method-assign, assignment]
        view = discord.ui.View(timeout=600)
        view.add_item(select)
        # fenetre-unique-globale-v1 (Drasah 30/10): joins the shared
        # single-window registry — no stacking with the player's
        # previous ephemeral view.
        await _open_unique_ephemeral(
            interaction,
            str(strings["remparts_place_guard_choose"]),
            view=view,
            ephemeral=True,
        )


class KingdomRempartsPatrouilleButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:remparts:patrouille:(?P<slot>[12])",
):
    """The restart-proof 🛡️ « Placer Patrouille 1 / 2 » button (remparts-v2).

    remparts-v2 (Drasah 14/10): the patrols are a SKILL — the Marché
    unlocks the slot (1 then 2), the King POSSES the daily 2h window
    here. The window stays FIXED for the whole cycle and may only be
    REDEFINED during the Jour du Seigneur (the service enforces the
    window). King only.
    """

    def __init__(self, slot: int, strings: dict[str, Any]) -> None:
        super().__init__(
            discord.ui.Button(
                label=str(strings["remparts_patrouille_button"]).format(slot)[:80],
                custom_id=f"kingdoms:remparts:patrouille:{slot}",
                style=discord.ButtonStyle.secondary,
            )
        )
        self.slot = int(slot)

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomRempartsPatrouilleButton:
        """Rebuild the patrol button from the wire."""
        locale = str(interaction.locale) if interaction.locale else "en"
        return cls(int(match.group("slot")), _strings(locale))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the King-only tranche select and place the patrol window."""
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
        kingdom_id = king.kingdom_id or ""
        select: discord.ui.Select[Any] = discord.ui.Select(
            custom_id=f"kingdoms:remparts:choose:patrouille:{self.slot}",
            placeholder=str(strings["market_choose_slot"])[:100],
            options=[
                discord.SelectOption(
                    label=str(strings["market_slot_line"]).format(start, start + 2),
                    value=str(start),
                )
                for start in range(0, 24, 2)
            ],
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
            slot = int(chosen[0])
            try:
                await economy.place_patrouille(kingdom_id, self.slot, slot)
            except Exception as exc:
                await target.response.edit_message(
                    content=_market_error_note(exc, strings),
                    view=None,
                )
                _market_ttl(interaction, getattr(target, "message", None))
                return
            note = str(strings["remparts_patrouille_placed"]).format(
                self.slot, slot, slot + 2
            )
            await target.response.edit_message(content=note, view=None)
            _market_ttl(interaction, getattr(target, "message", None))
            await _refresh_realm_views_for_kingdom_safe(target.guild, kingdom_id)

        select.callback = on_choose  # type: ignore[method-assign, assignment]
        view = discord.ui.View(timeout=600)
        view.add_item(select)
        # fenetre-unique-globale-v1 (Drasah 30/10): joins the shared
        # single-window registry — no stacking with the player's
        # previous ephemeral view.
        await _open_unique_ephemeral(
            interaction,
            str(strings["market_choose_slot"]),
            view=view,
            ephemeral=True,
        )


class KingdomFFAJoinButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:ffa:join",
):
    """The restart-proof 🗺️ « Aller explorer » button (ffa-exploration-v1).

    Drasah 14/10/2026: the Saturday FFA Exploration announcement in
    Géopolitique carries this persistent button. Any lord of the
    season may click to RESERVE a free slot; a second click before
    the start RELEASES it (the toggle settles the open withdrawal
    point — a quitter never blocks a seat).
    """

    def __init__(self, strings: dict[str, Any]) -> None:
        super().__init__(
            discord.ui.Button(
                label=str(strings["ffa_join_button"])[:80],
                custom_id="kingdoms:ffa:join",
                style=discord.ButtonStyle.primary,
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomFFAJoinButton:
        """Rebuild the FFA join button from the wire."""
        locale = str(interaction.locale) if interaction.locale else "en"
        return cls(_strings(locale))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Toggle the lord's FFA slot (join/leave), ephemeral note."""
        from kingdoms.mods.kingdoms.attacks import LordLockedError
        from kingdoms.mods.kingdoms.ffa_exploration import (
            FFA_JOINED,
            FFAFullError,
            FFAKingdomFullError,
            FFANotOpenError,
        )

        locale = str(interaction.locale) if interaction.locale else "en"
        strings = _strings(locale)
        wiring = _wiring()
        ffa_service = wiring.ffa_service
        if ffa_service is None:
            await interaction.response.send_message(strings["market_no_service"], ephemeral=True)
            return
        try:
            state, ffa = await ffa_service.enroll(str(interaction.user.id))
        except FFANotOpenError:
            await interaction.response.send_message(strings["ffa_not_open"], ephemeral=True)
            return
        except FFAFullError:
            await interaction.response.send_message(
                str(strings["ffa_full"]).format(max=ffa_service.max_participants()),
                ephemeral=True,
            )
            return
        except FFAKingdomFullError:
            await interaction.response.send_message(strings["ffa_kingdom_full"], ephemeral=True)
            return
        except LordLockedError:
            await interaction.response.send_message(strings["ffa_locked"], ephemeral=True)
            return
        except Exception as exc:
            await interaction.response.send_message(
                _market_error_note(exc, strings), ephemeral=True
            )
            return
        count = len(ffa.participants)
        maximum = ffa_service.max_participants()
        if state == FFA_JOINED:
            note = str(strings["ffa_join_note"]).format(
                interaction.user.display_name, count, maximum
            )
        else:
            note = str(strings["ffa_left_note"]).format(
                interaction.user.display_name, count, maximum
            )
        await interaction.response.send_message(note, ephemeral=True)


async def _traquenard_open_flow(
    interaction: discord.Interaction,
    wiring: KingdomsPanelWiring,
    strings: dict[str, Any],
    lord: Any,
) -> None:
    """Open the ephemeral adversary select of the Traquenard.

    chambre-utiliser-v1 (Drasah 30/10): extracted from the salon
    button — the 🚀 Utiliser shortcut of the Chambre des comptes
    rides the SAME flow (the bought charge is consumed at the
    activation, never by opening the window).
    """
    if wiring.attacks_service is None or wiring.kingdoms_service is None:
        await interaction.response.send_message(strings["market_no_service"], ephemeral=True)
        return
    kingdom_id = lord.kingdom_id or ""
    kingdoms = await wiring.kingdoms_service.kingdoms()
    options = [
        discord.SelectOption(label=str(getattr(k, "name", "") or k.kingdom_id)[:100], value=k.kingdom_id[:100])
        for k in kingdoms
        if not getattr(k, "is_gaia", False) and k.kingdom_id != kingdom_id
    ][:25]
    if not options:
        await interaction.response.send_message(strings["traquenard_no_target"], ephemeral=True)
        return
    select: discord.ui.Select[Any] = discord.ui.Select(
        custom_id="kingdoms:traquenard:choose",
        placeholder=str(strings["traquenard_choose"])[:100],
        options=options,
    )

    async def on_choose(target: discord.Interaction[Any]) -> None:
        values = getattr(target, "data", None) or {}
        chosen = [str(v) for v in values.get("values", [])]
        if not chosen:
            return
        attacks = wiring.attacks_service
        if attacks is None:
            await target.response.edit_message(
                content=strings["market_no_service"], view=None
            )
            return
        try:
            victim = await attacks.activate_traquenard(
                str(target.user.id), chosen[0]
            )
        except Exception as exc:
            await target.response.edit_message(
                content=str(strings["traquenard_failed"]).format(
                    message=getattr(exc, "message", str(exc))
                ),
                view=None,
            )
            return
        until = datetime.fromtimestamp(
            int(getattr(victim, "traquenard_until", 0) or 0),
            tz=ZoneInfo("Europe/Paris"),
        )
        note = str(strings["traquenard_done"]).format(
            kingdom=str(getattr(victim, "name", "") or chosen[0]),
            time=until.strftime("%d/%m %H:%M"),
        )
        await target.response.edit_message(content=note, view=None)
        _market_ttl(interaction, getattr(target, "message", None))
        await _refresh_realm_views_for_kingdom_safe(target.guild, kingdom_id)

    select.callback = on_choose  # type: ignore[method-assign, assignment]
    view = discord.ui.View(timeout=600)
    view.add_item(select)
    # fenetre-unique-royaume-v1 (Drasah 30/10): the select rides the
    # shared single-window registry — no stacking under a stale window.
    await _open_unique_ephemeral(
        interaction, str(strings["traquenard_choose"]), view=view, ephemeral=True
    )


class KingdomTraquenardButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:traquenard:activate",
):
    """The restart-proof 🪤 Traquenard button of the Le-Royaume view.

    marche-v2 article 2 (recâblage v2, Drasah 14/10): the Traquenard is
    INDEPENDENT of any declaration — any lord of the kingdom triggers it
    from the kingdom's own salon. It opens a 6h Facile-AI window on the
    chosen adversary kingdom (every attack declared in that window faces
    the level-1 AI) and adds +2h of lock on the target's already-sealed
    marriages. The button is only rendered while the kingdom holds at
    least one bought charge.
    """

    def __init__(self, strings: dict[str, Any]) -> None:
        super().__init__(
            discord.ui.Button(
                label=str(strings["traquenard_button"])[:80],
                custom_id="kingdoms:traquenard:activate",
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
    ) -> KingdomTraquenardButton:
        """Rebuild the Traquenard button from the wire."""
        locale = str(interaction.locale) if interaction.locale else "en"
        return cls(_strings(locale))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the ephemeral adversary-kingdom select of the Traquenard."""
        locale = str(interaction.locale) if interaction.locale else "en"
        strings = _strings(locale)
        wiring = _wiring()
        lord = await _lord_of(wiring.kingdoms_service, str(interaction.user.id))
        if lord is None:
            await interaction.response.send_message(strings["market_not_king"], ephemeral=True)
            return
        await _traquenard_open_flow(interaction, wiring, strings, lord)



def _market_ledger_text(wiring: KingdomsPanelWiring, kingdom: Any, strings: dict[str, Any]) -> str:
    """Build the chambre-des-comptes register (balance + reverse purchases)."""
    registry = list(getattr(kingdom, "market_registry", None) or [])
    spent = sum(int(entry.get("cost", 0) or 0) for entry in registry)
    wallet = int(getattr(kingdom, "tech_points_bank", 0) or 0)
    lines = [str(strings["market_ledger_summary"]).format(spent, wallet)]

    limits_parts: list[str] = []
    limits = getattr(_market_costs(wiring), "limits", None) or {}
    for item, limit in limits.items():
        limit = int(limit or 0)
        if limit <= 0:
            continue
        bought = sum(1 for entry in registry if entry.get("item") == item)
        limits_parts.append(f"{_market_label(item, strings)} {min(bought, limit)}/{limit}")
    if limits_parts:
        lines.append(str(strings["market_ledger_limits"]).format(" · ".join(limits_parts)))

    # chambre-des-comptes-v2 (Drasah 29/10): the purchases now ride a
    # rich ephemeral view — they get their own section header.
    lines.append("")
    lines.append(f"## {strings['market_ledger_purchases_title']}")
    if not registry:
        lines.append(str(strings["market_ledger_empty"]))
    else:
        emojis = strings.get("market_ledger_emoji") or {}
        for entry in sorted(registry, key=lambda e: int(e.get("at", 0) or 0), reverse=True):
            item = str(entry.get("item", ""))
            at = int(entry.get("at", 0) or 0)
            when = datetime.fromtimestamp(at, tz=ZoneInfo("Europe/Paris")).strftime("%d/%m %H:%M")
            lines.append(
                str(strings["market_ledger_line"]).format(
                    emojis.get(item, "•"),
                    _market_label(item, strings),
                    str(entry.get("detail", "")),
                    int(entry.get("cost", 0) or 0),
                    when,
                )
            )
    return "\n".join(lines)


# chambre-des-comptes-v2 (Drasah 29/10): the consumable market cards
# shown « in hand », in the market's shelf order — the counter is the
# REMAINING stock (``purchases``), never the season total (Drasah:
# « le nombre restant »).
# chambre-utiliser-v1 (Drasah 30/10): the hand also lists the TOKEN
# cards (corruption / jeu d'armes / explorateur) — their stock lives
# under a dedicated counter, the buys under the article counter.
_LEDGER_CARD_KEYS = (
    "sabotage",
    "contre_espionnage",
    "embuscade",
    "traquenard",
    "corruption",
    "jeu_d_armes",
    "explorateur",
)
_LEDGER_CARD_STOCK_KEYS = {
    "corruption": "corruption_stock",
    "jeu_d_armes": "jeu_d_armes_stock",
    "explorateur": "explorateur_stock",
}


async def _market_ledger_cards(
    wiring: KingdomsPanelWiring, kingdom_id: str, strings: dict[str, Any]
) -> list[tuple[str, str, int]]:
    """One entry per card type still held: (card key, text line, stock).

    chambre-des-comptes-v3 (Drasah 29/10): the key rides along so the
    ledger can attach the card's TCG art next to its line — he wants
    the cards VISIBLE (« l'image concrète plutôt que textuel »).
    v4: the raw stock rides along too — the MOSAIC painter stamps each
    card's X<stock> counter onto its tile.
    """
    purchases: dict[str, Any] = {}
    if wiring.attacks_service is not None and kingdom_id:
        try:
            state = await wiring.attacks_service.technology_state(kingdom_id)
            purchases = dict(getattr(state, "purchases", None) or {})
        except Exception:
            logger.info("KINGDOMS MARKET: card stock read failed")
    emojis = strings.get("market_ledger_emoji") or {}
    entries: list[tuple[str, str, int]] = []
    for key in _LEDGER_CARD_KEYS:
        stock = int(purchases.get(_LEDGER_CARD_STOCK_KEYS.get(key, key), 0) or 0)
        if stock <= 0:
            continue
        entries.append(
            (
                key,
                str(strings["market_ledger_card_line"]).format(
                    emojis.get(key, "•"), _market_label(key, strings), stock
                ),
                stock,
            )
        )
    return entries


def _market_ledger_card_files(held: list[tuple[str, str, int]]) -> list[discord.File]:
    """The card BACK at original size — ONE attachment (v6, Drasah 29/10).

    v5's mosaic still was not welcome: Drasah wants the window to show
    the card BACK by default (taille d'origine), with one button per
    held card; clicking a button EDITS the window in place to reveal
    that card's art + effect + usage. Empty hand = no attachment.
    """
    if not held:
        return []
    back = _market_card_back_file()
    return [back] if back is not None else []


async def _market_ledger_content(
    wiring: KingdomsPanelWiring, kingdom: Any, strings: dict[str, Any]
) -> tuple[str, list[discord.File], discord.ui.View | None]:
    """Build the chambre-des-comptes ephemeral sheet (v2→v3, Drasah 29/10).

    Top to bottom: the season balance (spent / treasury / limits), the
    purchases in reverse chronology, then the CARDS IN HAND — one line
    per card type with the remaining stock, deduplicated (multiple
    buys of the same card read ONE line + its counter). v3: the held
    cards also travel as their TCG art in attachments under the view
    (Discord cannot inline them in the text), the lines keep carrying
    the quantity. Returns (content, card files).
    """
    name = str(getattr(kingdom, "name", "") or "")
    lines = [
        f"# {str(strings['market_ledger_title']).format(name)}",
        "",
        _market_ledger_text(wiring, kingdom, strings),
    ]
    lines.append("")
    lines.append(f"## {strings['market_ledger_cards_title']}")
    held = await _market_ledger_cards(wiring, str(getattr(kingdom, "id", "")), strings)
    if held:
        lines.extend(line for _key, line, _stock in held)
    else:
        lines.append(str(strings["market_ledger_cards_empty"]))
    card_files = _market_ledger_card_files(held)
    # chambre-des-comptes-v6 (Drasah 29/10): the window shows the card
    # BACK (original size) by default and one secondary button per
    # held card type, labelled with the CARD NAME alone — clicking a
    # button EDITS the window in place (art + Effet + Usage), zero new
    # ephemeral, zero pollution.
    card_info_view: discord.ui.View | None = None
    if held:
        card_info_view = discord.ui.View(timeout=None)
        for key, _line, _stock in held:
            card_info_view.add_item(KingdomMarketCardInfoButton(key, strings))
    return "\n".join(lines), card_files, card_info_view


async def _market_card_stock(
    wiring: KingdomsPanelWiring, kingdom_id: str, key: str
) -> int:
    """The REMAINING stock of one card key (0 on any read failure)."""
    if wiring.attacks_service is None or not kingdom_id:
        return 0
    try:
        state = await wiring.attacks_service.technology_state(kingdom_id)
        purchases = dict(getattr(state, "purchases", None) or {})
        return int(purchases.get(key, 0) or 0)
    except Exception:
        logger.info("KINGDOMS MARKET: card stock read failed")
        return 0


class KingdomMarketCardInfoButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:market:cardinfo:(?P<card>[a-z_]+)",
):
    """chambre-des-comptes-v6 (Drasah 29/10): one secondary button per
    held card, labelled with the card's name. Clicking it EDITS the
    ledger window IN PLACE (type 7 semantics — the button sits ON the
    window, so the edit lands on the window itself, never the panel):
    the card-back default image becomes that card's TCG art, and the
    sheet gains the card's EFFET + USAGE lines. Zero new ephemeral =
    anti-pollution by construction. Restart-proof (DynamicItem)."""

    def __init__(
        self, card: str, strings: dict[str, Any], row: int | None = None
    ) -> None:
        # v6: the label is the CARD NAME alone (« un bouton "nomdelatech" »).
        label = _market_label(card, strings)
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                custom_id=f"kingdoms:market:cardinfo:{card}",
                style=discord.ButtonStyle.secondary,
                row=row,
            )
        )
        self.card = card

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomMarketCardInfoButton:
        """Rebuild the info button from the wire."""
        locale = str(interaction.locale) if interaction.locale else "en"
        return cls(match["card"], _strings(locale))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Reveal the card INSIDE the ledger window (edit in place)."""
        locale = str(interaction.locale) if interaction.locale else "en"
        strings = _strings(locale)
        wiring = _wiring()
        lord = await _lord_of(wiring.kingdoms_service, str(interaction.user.id))
        kingdom_id = str(getattr(lord, "kingdom_id", "") or "") if lord else ""
        kingdom = (
            await _kingdom_by_id_safe(wiring.kingdoms_service, kingdom_id)
            if kingdom_id
            else None
        )
        if kingdom is None:
            await interaction.response.send_message(
                strings["church_marry_kingdom_none"], ephemeral=True
            )
            return
        # Type-6 defer on the window's own component: the interaction's
        # original response IS the ledger window (never the panel —
        # the panel carries no cardinfo button), so the rebuild may take
        # its time and the edit below lands on the window itself.
        await interaction.response.defer()
        content, _card_files, card_info_view = await _market_ledger_content(
            wiring, kingdom, strings
        )
        emojis = strings.get("market_ledger_emoji") or {}
        fiche = [
            "",
            f"## {emojis.get(self.card, '•')} "
            f"{_market_label(self.card, strings)}",
            str(strings["market_card_effect"]).format(
                (strings.get("market_effect") or {}).get(self.card, "")
            ),
            str(strings["market_card_usage"]).format(
                (strings.get("market_usage") or {}).get(self.card, "")
            ),
        ]
        # chambre-utiliser-v1 (Drasah 30/10): revealing a USABLE card
        # adds the 🚀 Utiliser button — the effect fires HERE, from the
        # Chambre des comptes, never at the Marché.
        if card_info_view is not None and self.card in _MARKET_USABLE_CARDS:
            card_info_view.add_item(KingdomMarketUseButton(self.card, strings))
        # The chosen card's art, or the back when the art is missing
        # (cosmetic — the text lines still ride the edit).
        file = _market_card_file(self.card) or _market_card_back_file()
        files = [file] if file is not None else []
        # fix-v1 (live traceback 29/10 16h48): discord.py 2.7's
        # edit_original_response takes NO ``files`` kwarg — a NEW
        # attachment rides ``attachments`` as a File (an empty list
        # clears the previous image instead).
        await interaction.edit_original_response(
            content="\n".join([content, *fiche]),
            attachments=files,
            view=card_info_view,
        )


_MARKET_USABLE_CARDS = (
    "corruption",
    "jeu_d_armes",
    "explorateur",
    "traquenard",
    "embuscade",
)
"""chambre-utiliser-v1: the cards carrying the 🚀 Utiliser button."""


class KingdomMarketUseButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=(
        r"kingdoms:market:use:(?P<card>corruption|jeu_d_armes"
        r"|explorateur|traquenard|embuscade)"
    ),
):
    """chambre-utiliser-v1 (Drasah 30/10): the 🚀 Utiliser button of a
    revealed card — the EFFECT never fires at the Marché, it fires
    here, from the Chambre des comptes window. The corruption opens its
    territory select, the explorateur OPENS THE 🧭 EXPEDITION SALON
    (exploration-expedition-v1 — the token is consumed at the Lâcher
    les amarres, never here), the arms-mastery applies immediately, the
    traquenard opens its adversary select and the embuscade jumps to
    the attack-declaration wizard. Restart-proof (DynamicItem)."""

    def __init__(self, card: str, strings: dict[str, Any], row: int | None = None) -> None:
        super().__init__(
            discord.ui.Button(
                label=str(strings["market_use_button"])[:80],
                custom_id=f"kingdoms:market:use:{card}",
                style=discord.ButtonStyle.success,
                row=row,
            )
        )
        self.card = card

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomMarketUseButton:
        """Rebuild the use button from the wire."""
        locale = str(interaction.locale) if interaction.locale else "en"
        return cls(match["card"], _strings(locale))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Fire the card's effect from the ledger window."""
        locale = str(interaction.locale) if interaction.locale else "en"
        strings = _strings(locale)
        wiring = _wiring()
        lord = await _lord_of(wiring.kingdoms_service, str(interaction.user.id))
        if lord is None:
            await interaction.response.send_message(
                strings["church_marry_kingdom_none"], ephemeral=True
            )
            return
        # The token cards are KINGDOM-level strategic actions bought by
        # the King — their USE is King-only (the traquenard keeps its
        # any-lord rule, the embuscade its declaration-flow rules).
        if self.card in ("corruption", "explorateur", "jeu_d_armes") and str(
            getattr(lord, "role", "") or ""
        ) != "king":
            await interaction.response.send_message(
                strings["market_not_king"], ephemeral=True
            )
            return
        if self.card == "corruption":
            view = await _market_action_select_view(
                wiring, lord, "corruption", strings, anchor=interaction
            )
            if view is None:
                await interaction.response.send_message(
                    strings["market_no_options"], ephemeral=True
                )
                return
        # fenetre-unique-globale-v1 (Drasah 30/10): joins the shared
        # single-window registry — no stacking with the player's
        # previous ephemeral view.
            await _open_unique_ephemeral(
                interaction,
                str(strings["market_choose_corrupt"]),
                view=view,
                ephemeral=True,
            )
            return
        if self.card == "explorateur":
            # exploration-expedition-v1 (Drasah 30/10): the explorateur
            # token now funds a whole EXPEDITION — the Utiliser click
            # opens the 🧭 salon instead of the old lord select (the
            # token is consumed at the Lâcher les amarres, never here).
            from kingdoms.mods.kingdoms.kingdom_expedition_panel import (
                open_expedition_from_market,
            )

            await open_expedition_from_market(interaction, wiring, strings, lord)
            return
        if self.card == "jeu_d_armes":
            economy = wiring.economy_service
            if economy is None:
                await interaction.response.send_message(
                    strings["market_no_service"], ephemeral=True
                )
                return
            await interaction.response.defer()
            kingdom_id = str(getattr(lord, "kingdom_id", "") or "")
            try:
                level = await economy.use_jeu_d_armes(kingdom_id)
            except Exception as exc:
                note = await _market_failure_note(
                    exc,
                    wiring,
                    lord,
                    "jeu_d_armes",
                    int(getattr(_market_costs(wiring), "jeu_d_armes", 0) or 0),
                    strings,
                )
                await interaction.followup.send(note, ephemeral=True)
                return
            # The ledger window refreshes in place (the stock moved) and
            # the result rides a small followup bubble.
            kingdom = await _kingdom_by_id_safe(wiring.kingdoms_service, kingdom_id)
            if kingdom is not None:
                content, files, ledger_view = await _market_ledger_content(
                    wiring, kingdom, strings
                )
                try:
                    await interaction.edit_original_response(
                        content=content, attachments=files, view=ledger_view
                    )
                except Exception:
                    logger.warning(
                        "KINGDOMS MARKET: ledger refresh after use failed",
                        exc_info=True,
                    )
            note = str(strings["market_use_jeu_d_armes_ok"]).format(level)
            await interaction.followup.send(note, ephemeral=True)
            return
        if self.card == "traquenard":
            await _traquenard_open_flow(interaction, wiring, strings, lord)
            return
        if self.card == "embuscade":
            # A pure SHORTCUT: the declare wizard opens here, exactly as
            # the « Déclarer une attaque » button does (the card is
            # consumed by the declaration itself, never by this click).
            from kingdoms.mods.kingdoms.kingdom_attack_panel import (
                _attack_mode_select_view,
                _strings as attack_strings,
            )

            if wiring.attacks_service is None:
                await interaction.response.send_message(
                    strings["market_no_service"], ephemeral=True
                )
                return
            own_kingdom_id = str(getattr(lord, "kingdom_id", "") or "")
            mode_content, mode_view = await _attack_mode_select_view(
                wiring, own_kingdom_id, attack_strings(locale), interaction
            )
            if mode_view is None:
                await interaction.response.send_message(
                    strings["market_no_options"], ephemeral=True
                )
                return
        # fenetre-unique-globale-v1 (Drasah 30/10): joins the shared
        # single-window registry — no stacking with the player's
        # previous ephemeral view.
            await _open_unique_ephemeral(
                interaction, mode_content, view=mode_view, ephemeral=True
            )
            return


class KingdomMarketLedgerButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:market:ledger",
):
    """The restart-proof chambre-des-comptes button (Le-Royaume view)."""

    def __init__(self, label: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                custom_id="kingdoms:market:ledger",
                style=discord.ButtonStyle.secondary,
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomMarketLedgerButton:
        """Rebuild the ledger button from the wire."""
        locale = str(interaction.locale) if interaction.locale else "en"
        return cls(_strings(locale)["market_ledger_button"])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the ledger in THE player's single ephemeral window (v2)."""
        locale = str(interaction.locale) if interaction.locale else "en"
        strings = _strings(locale)
        wiring = _wiring()
        lord = await _lord_of(wiring.kingdoms_service, str(interaction.user.id))
        if lord is None:
            await interaction.response.send_message(
                strings["church_marry_kingdom_none"], ephemeral=True
            )
            return
        kingdom = await _kingdom_by_id_safe(wiring.kingdoms_service, str(lord.kingdom_id))
        if kingdom is None:
            await interaction.response.send_message(
                strings["church_marry_kingdom_none"], ephemeral=True
            )
            return
        # chambre-des-comptes-v2/v3 (Drasah 29/10): the read-only modal
        # becomes a rich ephemeral through the shared single-window
        # registry — balance, purchases and cards in hand (the held
        # cards' TCG art rides as attachments under the view), and the
        # previous window of the player closes instead of stacking.
        # chambre-des-comptes-fix-v1 (Drasah 29/10 live): the cold
        # mosaic paint takes seconds of pure-Python compositing — past
        # Discord's 3s interaction window the button died silent
        # (« chambre des comptes ne répond pas »). DEFER first (the
        # clock stops), THEN build, then the sheet rides a followup
        # through the shared single-window registry.
        await interaction.response.defer(ephemeral=True)
        content, card_files, card_info_view = await _market_ledger_content(
            wiring, kingdom, strings
        )
        user_id = str(getattr(interaction.user, "id", ""))
        await _close_previous_ephemeral(user_id)
        window = await interaction.followup.send(
            content, files=card_files, view=card_info_view, ephemeral=True
        )
        _EPHEMERAL_WINDOWS[user_id] = (interaction, time.monotonic(), window)


async def _lord_of(kingdoms_service: Any, user_id: str) -> Any | None:
    """Return the active lord record of the user (any role, None otherwise)."""
    if kingdoms_service is None:
        return None
    try:
        lords = await kingdoms_service.lords()
    except Exception:
        logger.warning("KINGDOMS PARISH: lord lookup failed", exc_info=True)
        return None
    return next(
        (item for item in lords if str(item.id) == str(user_id) and not item.left),
        None,
    )


async def _record_clicked_chronicle(
    wiring: KingdomsPanelWiring,
    user_id: str,
    kind: str,
    **params: str,
) -> None:
    """BEST-EFFORT chronicle on the CLICKING lord (Seigneurie V2 rule).

    Marché/Paroisse/Cathédrale: the entry lands on the lord who really
    clicked (the King), never on the kingdom — a chronicle failure is
    logged but never breaks the game action it follows.
    """
    if wiring.kingdoms_service is None:
        return
    try:
        from kingdoms.mods.kingdoms import chronicle
        from kingdoms.mods.kingdoms.game_clock import game_now

        lord = await _lord_of(wiring.kingdoms_service, str(user_id))
        if lord is None:
            return
        await chronicle.record(
            lord,
            kind,
            now=game_now(),
            store=wiring.kingdoms_service._store,
            **params,
        )
    except Exception:
        logger.info("KINGDOMS: chronicle record skipped", exc_info=True)


async def _kingdom_by_id_safe(kingdoms_service: Any, kingdom_id: str) -> Any | None:
    """Return the kingdom document behind a defensive lookup (None on failure)."""
    if kingdoms_service is None or not kingdom_id:
        return None
    try:
        return next(
            (k for k in await kingdoms_service.kingdoms() if str(k.id) == str(kingdom_id)),
            None,
        )
    except Exception:
        logger.warning("KINGDOMS PARISH: kingdom lookup failed", exc_info=True)
        return None


def _parish_tier_of(kingdoms_service: Any, kingdom: Any) -> Any:
    """Return the parish palier entry of a kingdom (defensive default)."""
    settings = getattr(getattr(kingdoms_service, "config", None), "parish", None)
    if settings is None:
        return SimpleNamespace(marriage_lock_hours=24, offering=0, display_name="Chapelle")
    return settings.tier(getattr(kingdom, "parish_tier", 1))


async def _church_marry_civ_view(
    wiring: KingdomsPanelWiring,
    lord: Any,
    kingdom: Any,
    strings: dict[str, Any],
) -> discord.ui.View | None:
    """Build the ephemeral civilization select of the standard marriage (D59).

    Every bachelor lord of the kingdom marries THEMSELVES (Drasah's
    2026-10-11 rule): the options are the kingdom's playable hand
    minus every civilization already claimed by an active marriage
    (exclusivity D74, all kingdoms).
    """
    if wiring.diplomacy_service is None or wiring.kingdoms_service is None:
        return None
    married = {
        str(item.married_civilization)
        for item in await wiring.kingdoms_service.lords()
        if getattr(item, "married_civilization", None) is not None
    }
    catalog = getattr(getattr(wiring.kingdoms_service, "config", None), "civilizations", ()) or ()
    options = [
        discord.SelectOption(
            label=str(getattr(civ, "display_name", civ.key))[:100],
            value=str(civ.key)[:100],
        )
        for civ in catalog
        if str(civ.key) in {str(c) for c in kingdom.civilizations}
        and str(civ.key) not in married
    ][:25]
    if not options:
        return None
    select: discord.ui.Select[Any] = discord.ui.Select(
        custom_id=f"kingdoms:church:marryciv:{str(lord.id)[:80]}",
        placeholder=strings["church_marry_intro"][:100],
        options=options,
    )
    diplomacy = wiring.diplomacy_service
    kingdoms_service = wiring.kingdoms_service
    kingdom_id = str(kingdom.id)
    tier = _parish_tier_of(kingdoms_service, kingdom)
    civ_names = {str(civ.key): str(getattr(civ, "display_name", civ.key)) for civ in catalog}

    async def on_choose(target: discord.Interaction[Any]) -> None:
        values = getattr(target, "data", None) or {}
        chosen = [str(v) for v in values.get("values", [])]
        if not chosen:
            return
        await target.response.defer(ephemeral=True)
        try:
            await diplomacy.marry(str(lord.id), chosen[0])
        except Exception as exc:
            await target.followup.send(_market_error_note(exc, strings), ephemeral=True)
            return
        # Drasah's livery (2026-10-11): the fresh spouse wears the red
        # 💍 Verrouillé role until the lock expires — zero notification.
        await _assign_marriage_lock_role_safe(target.guild, str(lord.id))
        # chef-mercenaire-v1 T2c: the sealed civ strips its mercenary
        # holders (fixed 1-écu refund DM).
        await _strip_mercenary_holders_safe(target.client, chosen[0], strings)
        royal = str(getattr(lord, "role", "")) == "king"
        hours = int(tier.marriage_lock_hours) * (2 if royal else 1)
        if royal:
            await target.followup.send(
                strings["church_marry_royal_ok"].format(
                    civ_names.get(chosen[0], chosen[0]),
                    hours,
                ),
                ephemeral=True,
            )
            lock_until = 0
            try:
                fresh_lord = next(
                    (
                        item
                        for item in await kingdoms_service.lords()
                        if str(item.id) == str(lord.id)
                    ),
                    None,
                )
                if fresh_lord is not None:
                    lock_until = int(
                        getattr(fresh_lord, "marriage_locked_until", 0) or 0
                    )
            except Exception:
                lock_until = 0
            await _announce_royal_wedding_safe(
                target.guild,
                strings,
                str(getattr(lord, "display_name", str(lord.id))),
                str(getattr(kingdom, "name", "")),
                civ_names.get(chosen[0], chosen[0]),
                hours,
                king_id=str(lord.id),
                lock_until=lock_until,
            )
        else:
            await target.followup.send(
                strings["church_marry_ok"].format(
                    civ_names.get(chosen[0], chosen[0]),
                    hours,
                    int(tier.offering),
                ),
                ephemeral=True,
            )
        await _refresh_realm_views_for_kingdom_safe(target.guild, kingdom_id)

    select.callback = on_choose  # type: ignore[method-assign, assignment]
    view = discord.ui.View(timeout=600)
    view.add_item(select)
    return view


class KingdomChurchMarryButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:church:marry",
):
    """The restart-proof 💍 Se marier button of the pinned Paroisse view.

    Every bachelor lord marries themselves (Drasah's rule): the button
    opens the civilization select of the kingdom's available hand.
    Consumes one point de mariage (D59) and credits the Offrande of
    the current palier (D64).
    """

    def __init__(self, strings: dict[str, Any]) -> None:
        super().__init__(
            discord.ui.Button(
                label=str(strings["church_marry_button"])[:80],
                custom_id="kingdoms:church:marry",
                style=discord.ButtonStyle.primary,
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomChurchMarryButton:
        """Rebuild the marry button from the wire."""
        locale = str(interaction.locale) if interaction.locale else "en"
        return cls(_strings(locale))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the civilization select for the clicking bachelor lord."""
        locale = str(interaction.locale) if interaction.locale else "en"
        strings = _strings(locale)
        wiring = _wiring()
        if wiring.kingdoms_service is None or wiring.diplomacy_service is None:
            await interaction.response.send_message(strings["market_no_service"], ephemeral=True)
            return
        lord = await _lord_of(wiring.kingdoms_service, str(interaction.user.id))
        if lord is None or lord.kingdom_id is None:
            await interaction.response.send_message(
                strings["church_marry_kingdom_none"], ephemeral=True
            )
            return
        kingdom = await _kingdom_by_id_safe(wiring.kingdoms_service, str(lord.kingdom_id))
        if kingdom is None:
            await interaction.response.send_message(
                strings["church_marry_kingdom_none"], ephemeral=True
            )
            return
        view = await _church_marry_civ_view(wiring, lord, kingdom, strings)
        if view is None:
            # Drasah's 2026-10-11 feedback: a bare "no options" note was
            # cryptic — say WHY: an empty Alliances hand waits for the
            # next Lord's Day draft, a fully-married hand just has no
            # free civilization left.
            note = (
                strings["church_err_no_hand"]
                if not list(getattr(kingdom, "civilizations", []) or [])
                else strings["market_no_options"]
            )
            await interaction.response.send_message(note, ephemeral=True)
            return
        # fenetre-unique-globale-v1 (Drasah 30/10): joins the shared
        # single-window registry — no stacking with the player's
        # previous ephemeral view.
        await _open_unique_ephemeral(
            interaction,
            str(strings["church_marry_intro"]),
            view=view,
            ephemeral=True,
        )


class KingdomChurchArrangeButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:church:arrange",
):
    """The restart-proof 💒 Mariage arrangé button of the Paroisse view.

    Shown from the Église palier (2/3): the King consumes ONE token of
    the kingdom's arranged-marriage stock (bought at the Marché, D60)
    — pick the bachelor lord, then the civilization.
    """

    def __init__(self, strings: dict[str, Any], stock: int) -> None:
        super().__init__(
            discord.ui.Button(
                label=str(strings["church_arrange_button"]).format(stock)[:80],
                custom_id="kingdoms:church:arrange",
                style=discord.ButtonStyle.secondary,
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomChurchArrangeButton:
        """Rebuild the arrange button (stock looked up best-effort)."""
        locale = str(interaction.locale) if interaction.locale else "en"
        strings = _strings(locale)
        stock = 0
        wiring = _wiring()
        if wiring.kingdoms_service is not None:
            king = await _king_of(wiring.kingdoms_service, str(interaction.user.id))
            if king is not None and king.kingdom_id:
                kingdom = await _kingdom_by_id_safe(
                    wiring.kingdoms_service, str(king.kingdom_id)
                )
                if kingdom is not None:
                    stock = int(getattr(kingdom, "arranged_marriage_stock", 0) or 0)
        return cls(strings, stock)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the lord select of the arranged marriage (King only)."""
        locale = str(interaction.locale) if interaction.locale else "en"
        strings = _strings(locale)
        wiring = _wiring()
        if wiring.kingdoms_service is None or wiring.diplomacy_service is None:
            await interaction.response.send_message(strings["market_no_service"], ephemeral=True)
            return
        king = await _king_of(wiring.kingdoms_service, str(interaction.user.id))
        if king is None:
            await interaction.response.send_message(
                strings["church_arrange_king_only"], ephemeral=True
            )
            return
        marry_view = await _market_marry_lord_view(wiring, king, strings)
        if marry_view is None:
            await interaction.response.send_message(strings["market_no_options"], ephemeral=True)
            return
        # fenetre-unique-globale-v1 (Drasah 30/10): joins the shared
        # single-window registry — no stacking with the player's
        # previous ephemeral view.
        await _open_unique_ephemeral(
            interaction,
            str(strings["market_choose_lord"]),
            view=marry_view,
            ephemeral=True,
        )


class KingdomChurchUpgradeButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:church:upgrade",
):
    """The restart-proof ⛪ Améliorer button of the Paroisse view.

    Direct always-succeeds buy (D64): pays the next palier's cost from
    the treasury — Chapelle → Église (2 🔬) → Cathédrale (4 🔬, Drasah
    2026-10-11 values). King only.
    """

    def __init__(self, strings: dict[str, Any], next_name: str, cost: int) -> None:
        if next_name:
            label = str(strings["church_upgrade_button"]).format(next_name, cost)[:80]
            disabled = False
        else:
            label = str(strings["church_upgrade_max"])[:80]
            disabled = True
        super().__init__(
            discord.ui.Button(
                label=label,
                custom_id="kingdoms:church:upgrade",
                style=discord.ButtonStyle.success,
                disabled=disabled,
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomChurchUpgradeButton:
        """Rebuild the upgrade button (palier looked up best-effort)."""
        locale = str(interaction.locale) if interaction.locale else "en"
        strings = _strings(locale)
        wiring = _wiring()
        next_name, cost = "", 0
        if wiring.kingdoms_service is not None:
            king = await _king_of(wiring.kingdoms_service, str(interaction.user.id))
            if king is not None and king.kingdom_id:
                kingdom = await _kingdom_by_id_safe(
                    wiring.kingdoms_service, str(king.kingdom_id)
                )
                if kingdom is not None:
                    settings = getattr(
                        getattr(wiring.kingdoms_service, "config", None), "parish", None
                    )
                    if settings is not None:
                        tier = int(getattr(kingdom, "parish_tier", 1) or 1)
                        tiers = settings.tiers
                        if tier < len(tiers):
                            next_name = str(tiers[tier].display_name)
                            cost = int(tiers[tier].cost)
        return cls(strings, next_name, cost)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Pay the next palier and re-render the Paroisse view."""
        locale = str(interaction.locale) if interaction.locale else "en"
        strings = _strings(locale)
        wiring = _wiring()
        if wiring.kingdoms_service is None:
            await interaction.response.send_message(strings["market_no_service"], ephemeral=True)
            return
        king = await _king_of(wiring.kingdoms_service, str(interaction.user.id))
        if king is None:
            await interaction.response.send_message(
                strings["church_upgrade_king_only"], ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            kingdom = await wiring.kingdoms_service.upgrade_parish(str(king.kingdom_id))
        except Exception as exc:
            await interaction.followup.send(_market_error_note(exc, strings), ephemeral=True)
            return
        await _record_clicked_chronicle(
            wiring, str(interaction.user.id), "parish_upgrade",
            tier=str(int(getattr(kingdom, "parish_tier", 1) or 1)),
        )
        wallet = int(getattr(kingdom, "tech_points_bank", 0) or 0)
        tier = _parish_tier_of(wiring.kingdoms_service, kingdom)
        tiers = getattr(
            getattr(wiring.kingdoms_service, "config", None), "parish", SimpleNamespace(tiers=())
        ).tiers
        max_tier = len(tiers)
        await interaction.followup.send(
            strings["church_upgrade_ok"].format(
                str(getattr(tier, "display_name", "")),
                int(getattr(kingdom, "parish_tier", 1) or 1),
                max_tier or 3,
                wallet,
            ),
            ephemeral=True,
        )
        await _refresh_realm_views_for_kingdom_safe(interaction.guild, str(kingdom.id))


class _ConsecrateSelect(discord.ui.Select["discord.ui.View"]):
    """The ephemeral map-key picker of the Consecrate flow (lieu-sacree-v1)."""

    def __init__(
        self,
        strings: dict[str, Any],
        cathedral_service: Any,
        kingdom: Any,
        options: list[discord.SelectOption],
    ) -> None:
        super().__init__(
            placeholder=str(strings["cathedral_consecrate_choose"])[:100],
            options=options,
        )
        self._strings = strings
        self._cathedral_service = cathedral_service
        self._kingdom = kingdom

    async def callback(self, interaction: discord.Interaction) -> None:
        """Consecrate the chosen territory and report (best effort)."""
        map_key = str(self.values[0])
        await _consecrate_and_report(interaction, self._strings, self._kingdom, map_key)


async def _consecrate_and_report(
    interaction: discord.Interaction,
    strings: dict[str, Any],
    kingdom: Any,
    map_key: str,
) -> None:
    """Run the consecration and report it (ephemeral note + Géopolitique)."""
    wiring = _wiring()
    cathedral_service = wiring.cathedral_service
    if cathedral_service is None:
        await interaction.response.send_message(strings["market_no_service"], ephemeral=True)
        return
    from kingdoms.mods.kingdoms.cathedrale import (
        CathedralActiveError,
        CathedralKingOnlyError,
        CathedralTierError,
    )

    await interaction.response.defer(ephemeral=True)
    try:
        territory = await cathedral_service.consecrate(str(interaction.user.id), map_key)
    except CathedralKingOnlyError:
        await interaction.followup.send(strings["cathedral_consecrate_king_only"], ephemeral=True)
        return
    except CathedralTierError:
        await interaction.followup.send(strings["cathedral_consecrate_tier"], ephemeral=True)
        return
    except CathedralActiveError:
        await interaction.followup.send(strings["cathedral_consecrate_none"], ephemeral=True)
        return
    except Exception as exc:
        await interaction.followup.send(_market_error_note(exc, strings), ephemeral=True)
        return
    await _record_clicked_chronicle(
        wiring, str(interaction.user.id), "cathedral_consecrated",
        territory=str(territory.map_key),
    )
    from zoneinfo import ZoneInfo

    until = territory.cathedral_until
    until_label = ""
    if until is not None:
        until_label = until.astimezone(ZoneInfo("Europe/Paris")).strftime("%d/%m %Hh%M")
    await interaction.followup.send(
        str(strings["cathedral_consecrate_ok"]).format(str(territory.map_key), until_label),
        ephemeral=True,
    )
    guild = interaction.guild
    if guild is not None:
        from kingdoms.mods.kingdoms.kingdom_content import announce_geopolitics

        try:
            await announce_geopolitics(
                guild,
                str(interaction.locale) if interaction.locale else "en",
                str(strings["cathedral_consecrated"]).format(str(kingdom.name), str(territory.map_key)),
            )
        except Exception:
            logger.info("KINGDOMS: consecration announcement skipped", exc_info=True)
    await _refresh_realm_views_for_kingdom_safe(guild, str(kingdom.id))


class KingdomChurchConsecrateButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:church:consecrate",
):
    """The restart-proof ⛪ Consecrate button of the Paroisse view (lieu-sacree-v1).

    King only, Cathédrale palier: pays 3 ⚙️ and poses the 72h chantier
    on one of the kingdom's territories — the ephemeral picker lists
    them, a single candidate consecrates directly.
    """

    def __init__(self, strings: dict[str, Any]) -> None:
        super().__init__(
            discord.ui.Button(
                label=str(strings["cathedral_consecrate_button"])[:80],
                custom_id="kingdoms:church:consecrate",
                style=discord.ButtonStyle.success,
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomChurchConsecrateButton:
        """Rebuild the consecrate button from the wire."""
        locale = str(interaction.locale) if interaction.locale else "en"
        return cls(_strings(locale))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Check King + palier, then consecrate (pick when several choices)."""
        from kingdoms.mods.kingdoms.cathedrale import CathedralError

        locale = str(interaction.locale) if interaction.locale else "en"
        strings = _strings(locale)
        wiring = _wiring()
        cathedral_service = wiring.cathedral_service
        if wiring.kingdoms_service is None or cathedral_service is None:
            await interaction.response.send_message(strings["market_no_service"], ephemeral=True)
            return
        king = await _king_of(wiring.kingdoms_service, str(interaction.user.id))
        if king is None:
            await interaction.response.send_message(
                strings["cathedral_consecrate_king_only"], ephemeral=True
            )
            return
        kingdom = await _kingdom_by_id_safe(wiring.kingdoms_service, str(king.kingdom_id))
        if kingdom is None:
            await interaction.response.send_message(strings["market_no_service"], ephemeral=True)
            return
        settings = getattr(
            getattr(wiring.kingdoms_service, "config", None), "parish", None
        )
        if settings is not None and int(getattr(kingdom, "parish_tier", 1) or 1) < len(settings.tiers):
            await interaction.response.send_message(
                strings["cathedral_consecrate_tier"], ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            candidates = await cathedral_service.consecratable(str(kingdom.kingdom_id))
        except CathedralError as exc:
            await interaction.followup.send(_market_error_note(exc, strings), ephemeral=True)
            return
        if not candidates:
            await interaction.followup.send(
                strings["cathedral_consecrate_none"], ephemeral=True
            )
            return
        if len(candidates) == 1:
            await _consecrate_and_report(interaction, strings, kingdom, str(candidates[0].map_key))
            return
        view = discord.ui.View(timeout=300)
        view.add_item(
            _ConsecrateSelect(
                strings,
                cathedral_service,
                kingdom,
                [
                    discord.SelectOption(label=str(item.map_key)[:100], value=str(item.map_key))
                    for item in candidates[:25]
                ],
            )
        )
        # fenetre-unique-globale-v1 (Drasah 30/10): the defer+followup
        # variant of the shared registry (3-tuple — close via the
        # followup message, never delete_original_response).
        user_id = str(getattr(interaction.user, "id", ""))
        await _close_previous_ephemeral(user_id)
        window = await interaction.followup.send(
            strings["cathedral_consecrate_choose"], view=view, ephemeral=True
        )
        _EPHEMERAL_WINDOWS[user_id] = (interaction, time.monotonic(), window)


async def _revoke_kingdoms_roles(guild: discord.Guild | None, user_id: str) -> None:
    """Strip the kingdoms mod roles of one player (shared by the select and the modal)."""
    wiring = _wiring()
    if wiring.mod_roles_service is None or guild is None:
        return
    for role_key in ("kingdoms_lord", "kingdoms_king"):
        try:
            await wiring.mod_roles_service.remove_mod_role(str(guild.id), user_id, "kingdoms", role_key)
        except Exception:
            logger.warning("KINGDOMS ADMIN: role removal failed for %s", role_key, exc_info=True)


MARRIAGE_LOCK_ROLE_NAME = "💍 Verrouillé"
MARRIAGE_LOCK_ROLE_COLOUR = 0xE74C3C
"""Drasah's lock livery (2026-10-11): a RED pseudo is the one visible
state the players read at a glance — no ping, no notification: a role
assignment never notifies anyone, the adversaries must OBSERVE."""

MARRIAGE_RING_ROLE_NAME = "💍"
"""The married badge (Drasah's rule, 2026-10-12): once the honeymoon
lock expires, a still-married lord keeps a 💍 RING badge next to their
pseudo. The role is colour-NEUTRAL — it never paints the pseudo, the
emoji alone carries the intel."""
_RING_NICK_SUFFIX = " 💍"
"""The ring IN the pseudo itself (Drasah's follow-up, 2026-10-12): a
role name emoji shows only in the profile role list, NEVER in the
nickname — only a nickname edit can paint the ring on the pseudo."""
_LOCK_NICK_SUFFIX = " 🔒"
"""The lock IN the pseudo itself (Drasah's rule, 2026-10-12): while the
honeymoon lock is ACTIVE the pseudo wears the 🔒 suffix on top of the
red role colour — the colour alone is invisible when a higher-ranked
role outranks the lock role, the emoji always shows."""


async def _ensure_marriage_lock_role(guild: discord.Guild | None) -> discord.Role | None:
    """Adopt or create the red 💍 Verrouillé role (never hoisted)."""
    if guild is None:
        return None
    role = discord.utils.get(guild.roles, name=MARRIAGE_LOCK_ROLE_NAME)
    if role is None:
        try:
            role = await guild.create_role(
                name=MARRIAGE_LOCK_ROLE_NAME,
                colour=discord.Colour(MARRIAGE_LOCK_ROLE_COLOUR),
                reason="kingdoms: mariage — rôle verrou visible",
            )
        except Exception:
            logger.warning("KINGDOMS: marriage lock role creation failed", exc_info=True)
            return None
    # Drasah's live finding (2026-10-12): the red pseudo never paints
    # when the lord's own roles (Roi…) sit ABOVE the lock role — Discord
    # colours the HIGHEST role only. Park the lock role just under the
    # bot's top role so it out-ranks every member role; best effort,
    # the scheduler's sync pass retries the edit every tick.
    bot_top = getattr(getattr(guild, "me", None), "top_role", None)
    if bot_top is not None:
        target_position = int(getattr(bot_top, "position", 0)) - 1
        if int(getattr(role, "position", 0)) < target_position:
            try:
                await role.edit(
                    position=target_position,
                    reason="kingdoms: rôle verrou au-dessus des rôles joueurs",
                )
            except Exception:
                logger.warning("KINGDOMS: lock role positioning failed", exc_info=True)
    return role


async def _ensure_marriage_ring_role(guild: discord.Guild | None) -> discord.Role | None:
    """Adopt or create the colour-neutral 💍 married badge (never hoisted).

    The ring carries NO colour (a default-colour role never paints the
    pseudo) — its emoji alone marks the married lord. Parked under the
    lock role so the badges read lock-first; best effort like the lock.
    """
    if guild is None:
        return None
    role = discord.utils.get(guild.roles, name=MARRIAGE_RING_ROLE_NAME)
    if role is None:
        try:
            role = await guild.create_role(
                name=MARRIAGE_RING_ROLE_NAME,
                reason="kingdoms: mariage — badge bague des seigneurs mariés",
            )
        except Exception:
            logger.warning("KINGDOMS: marriage ring role creation failed", exc_info=True)
            return None
    bot_top = getattr(getattr(guild, "me", None), "top_role", None)
    if bot_top is not None:
        target_position = int(getattr(bot_top, "position", 0)) - 2
        if int(getattr(role, "position", 0)) < target_position:
            try:
                await role.edit(
                    position=target_position,
                    reason="kingdoms: badge bague sous le rôle verrou",
                )
            except Exception:
                logger.warning("KINGDOMS: ring role positioning failed", exc_info=True)
    return role


async def _resolve_member(guild: discord.Guild, user_id: int) -> Any | None:
    """Find one member: cache first, REST fallback (Drasah's 2026-10-12).

    The bot runs WITHOUT the Discord "members" intent
    (``Intents.default()`` in the bot factory): ``guild.members`` stays
    empty and ``guild.get_member`` returns None for everyone — every
    livery assignment silently no-opped (the live 03:45 marriage logged
    NOTHING). ``fetch_member`` is a plain REST call that works without
    the intent: it is the reliable seam.
    """
    member = guild.get_member(user_id)
    if member is not None:
        return member
    try:
        return await guild.fetch_member(user_id)
    except Exception:
        return None


_LIVERY_AUDITED_GUILDS = set()  # type: set[int]


async def _log_livery_audit(guild: discord.Guild) -> None:
    """Log the role ladder + the bot's perms once per boot (diagnostic).

    Red needs the lock role to be the member's HIGHEST coloured role
    and the bot cannot move its own role (Discord rule): when the
    ladder is wrong the fix is server-side. The audit names the culprit
    so a single /logs read settles it.
    """
    guild_id = int(getattr(guild, "id", 0) or 0)
    if guild_id in _LIVERY_AUDITED_GUILDS:
        return
    _LIVERY_AUDITED_GUILDS.add(guild_id)
    roles = list(getattr(guild, "roles", []) or [])
    ladder = sorted(
        roles, key=lambda r: int(getattr(r, "position", 0) or 0), reverse=True
    )
    ladder_text = ", ".join(
        f"{getattr(r, 'name', '?')}@{int(getattr(r, 'position', 0) or 0)}"
        for r in ladder[:10]
    )
    bot_member = getattr(guild, "me", None)
    perms = getattr(bot_member, "guild_permissions", None)
    logger.info(
        "KINGDOMS LIVERY AUDIT (guild %s): ladder %s | bot top role %s"
        " | moderate_members=%s manage_roles=%s",
        guild_id,
        ladder_text,
        getattr(getattr(bot_member, "top_role", None), "name", "?"),
        bool(getattr(perms, "moderate_members", False)) if perms is not None else None,
        bool(getattr(perms, "manage_roles", False)) if perms is not None else None,
    )


async def _assign_marriage_lock_role_safe(guild: discord.Guild | None, user_id: str) -> None:
    """Give one lord the red lock role (best effort, zero notification)."""
    role = await _ensure_marriage_lock_role(guild)
    if role is None or guild is None or not str(user_id).isdigit():
        return
    member = await _resolve_member(guild, int(user_id))
    if member is None:
        logger.warning(
            "KINGDOMS: lock role skipped — member %s not found in guild %s",
            user_id,
            getattr(guild, "id", "?"),
        )
        return
    if role in getattr(member, "roles", ()):
        return
    # Diagnostic (Drasah's 2026-10-12 "pseudo never red" report):
    # Discord paints the HIGHEST role only — a member owning a role
    # above the lock role (admin/founder roles ranked above the bot)
    # keeps that role's colour. Say it once, at the marriage.
    top_role = getattr(member, "top_role", None)
    if top_role is not None and getattr(top_role, "id", None) != getattr(
        role, "id", None
    ):
        logger.info(
            "KINGDOMS: lock role is outranked by '%s' for member %s — the pseudo keeps the higher role's colour",
            getattr(top_role, "name", "?"),
            user_id,
        )
    try:
        await member.add_roles(role, reason="kingdoms: mariage — verrou de lune de miel")
    except Exception:
        logger.warning("KINGDOMS: marriage lock role assignment failed", exc_info=True)
        return
    # Drasah's follow-up (2026-10-12): the lock also lives IN the pseudo
    # (🔒 suffix) — the red colour is invisible when a higher role
    # outranks the lock role, the emoji always shows. Painted NOW so
    # the livery is complete at the marriage itself.
    await _sync_member_marriage_nickname(member, _LOCK_NICK_SUFFIX)


async def _remove_marriage_lock_role_safe(guild: discord.Guild | None, user_id: str) -> None:
    """Strip the red lock role of one lord (best effort)."""
    if guild is None or not str(user_id).isdigit():
        return
    role = discord.utils.get(guild.roles, name=MARRIAGE_LOCK_ROLE_NAME)
    if role is None:
        return
    member = await _resolve_member(guild, int(user_id))
    if member is None:
        logger.warning(
            "KINGDOMS: lock role strip skipped — member %s not found in guild %s",
            user_id,
            getattr(guild, "id", "?"),
        )
        return
    if role not in getattr(member, "roles", ()):
        return
    try:
        await member.remove_roles(role, reason="kingdoms: fin du verrou mariage")
    except Exception:
        logger.warning("KINGDOMS: marriage lock role removal failed", exc_info=True)


async def _sync_member_role(
    member: Any, role: discord.Role | None, should: bool, reason: str
) -> bool:
    """Add/strip one livery role to match the expected state.

    Returns True when the Discord state actually changed (the caller
    marks the kingdom for a view refresh). Best effort: a refused API
    call logs and leaves the state to the next tick.
    """
    if role is None:
        return False
    has = role in getattr(member, "roles", ())
    if has == should:
        return False
    try:
        if should:
            await member.add_roles(role, reason=reason)
        else:
            await member.remove_roles(role, reason=reason)
    except Exception:
        logger.warning("KINGDOMS: livery role sync failed (%s)", reason, exc_info=True)
        return False
    return True


def _member_lock_flags(lord: Any, now: int) -> tuple[bool, bool]:
    """Return the (lock, ring) livery flags of one member.

    The red lock livery covers the honeymoon (D59/D60) AND the 48h
    exploration (marche-v2 art. 6 — the lock is reused verbatim: no
    attack, no defence, red pseudo + 🔒). The ring only shows on a
    married member whose lock has expired; an exploring lord never
    wears both.
    """
    until = int(getattr(lord, "marriage_locked_until", 0) or 0) if lord is not None else 0
    exploring_until = (
        int(getattr(lord, "exploration_locked_until", 0) or 0) if lord is not None else 0
    )
    married = bool(
        lord is not None
        and not getattr(lord, "left", False)
        and getattr(lord, "married_civilization", None) is not None
    )
    should_lock = exploring_until > now or (married and until > now)
    return should_lock, married and not should_lock


async def sync_marriage_lock_roles_safe(guild: discord.Guild | None) -> set[str]:
    """Reconcile the lock/ring livery with the live lord documents.

    The scheduler calls this every tick — it is the boot resync AND the
    expiry engine in one idempotent pass. Drasah's full rule
    (2026-10-12): an ACTIVE lock wears the RED 💍 Verrouillé pseudo
    AND the 🔒 suffix in the nickname; an EXPIRED lock on a
    still-married lord swaps red for the colour-neutral 💍 ring badge
    AND the 💍 suffix on the pseudo itself; an unmarried/ejected/
    departed lord wears nothing. A restart never leaves a ghost. Returns the kingdom ids whose Discord
    state changed — the caller re-renders their salon views in place
    (an edit never pings anyone).
    """
    import time as _time

    wiring = _wiring()
    service = wiring.kingdoms_service
    if guild is None or service is None:
        return set()
    try:
        lords = await service.lords()
    except Exception:
        logger.warning("KINGDOMS: lord list read failed (lock sync)", exc_info=True)
        return set()
    lock_role = await _ensure_marriage_lock_role(guild)
    ring_role = await _ensure_marriage_ring_role(guild)
    if lock_role is None and ring_role is None:
        return set()
    await _log_livery_audit(guild)
    now = int(_time.time())
    by_id = {str(getattr(lord, "id", "")): lord for lord in lords}
    # The bot runs without the members intent (see _resolve_member): the
    # gateway cache is EMPTY on the live guild — the pass is driven by
    # the LORD documents instead, each member resolved cache-then-REST.
    members: dict[str, Any] = {}
    for member in list(getattr(guild, "members", []) or []):
        members[str(getattr(member, "id", ""))] = member
    for lord_id in by_id:
        if lord_id in members or not lord_id.isdigit():
            continue
        fetched = await _resolve_member(guild, int(lord_id))
        if fetched is not None:
            members[lord_id] = fetched
    changed: set[str] = set()
    for member in members.values():
        lord = by_id.get(str(getattr(member, "id", "")))
        should_lock, should_ring = _member_lock_flags(lord, now)
        nick_suffix = (
            _LOCK_NICK_SUFFIX if should_lock else _RING_NICK_SUFFIX if should_ring else None
        )
        lock_moved = await _sync_member_role(
            member, lock_role, should_lock, "kingdoms: mariage — verrou de lune de miel"
        )
        ring_moved = await _sync_member_role(
            member, ring_role, should_ring, "kingdoms: mariage — badge bague"
        )
        # A member with no lord document (ejected mid-season, wiped
        # season) is stripped of every marriage suffix — a lordless
        # member never wears the livery.
        nick_moved = await _sync_member_marriage_nickname(member, nick_suffix)
        if (lock_moved or ring_moved or nick_moved) and lord is not None:
            changed.add(str(getattr(lord, "kingdom_id", "") or ""))
    return changed


def _locale_of_strings(strings: dict[str, Any]) -> str:
    """Derive the locale of one merged strings dict (lock_days sentinel)."""
    return "fr" if str(strings.get("lock_days", "")).startswith("lundi") else "en"


async def _sync_member_marriage_nickname(member: Any, suffix: str | None) -> bool:
    """Paint/strip the marriage suffix of one member's nickname.

    Drasah's full pseudo rule (2026-10-12): an ACTIVE lock wears the 🔒
    suffix (on top of the red role colour), an EXPIRED lock on a
    still-married lord wears the 💍 suffix, anything else wears none.
    The pass is idempotent (the suffixes never stack, whichever of the
    two is present is stripped first) and only ever appends/strips its
    OWN suffixes: a hand-made nickname is left alone unless it ends
    with one of them. Returns True when the nickname actually changed.
    """
    current = getattr(member, "nick", None)
    base = str(current) if current is not None else str(getattr(member, "name", ""))
    if not base:
        return False
    if suffix and base.endswith(suffix):
        return False  # idempotent: the target suffix is already worn
    changed = False
    for known in (_RING_NICK_SUFFIX, _LOCK_NICK_SUFFIX):
        if base.endswith(known):
            base = base[: -len(known)]
            changed = True
    if suffix and base:
        target = base + suffix
        if len(target) > 32:
            logger.info(
                "KINGDOMS: marriage nickname skipped for %s (over 32 chars)", base
            )
            return changed
        try:
            await member.edit(nick=target, reason="kingdoms: mariage — pseudo")
        except Exception:
            logger.warning("KINGDOMS: marriage nickname edit failed", exc_info=True)
            return changed
        return True
    if changed:
        try:
            await member.edit(
                nick=base or None,
                reason="kingdoms: fin de mariage — pseudo nettoyé",
            )
        except Exception:
            logger.warning("KINGDOMS: marriage nickname strip failed", exc_info=True)
            return False
        return True
    return False


async def _announce_royal_wedding_safe(
    guild: discord.Guild | None,
    strings: dict[str, Any],
    king_name: str,
    kingdom_name: str,
    civ_name: str,
    hours: int,
    *,
    king_id: str = "",
    lock_until: int = 0,
) -> None:
    """Announce a royal wedding in Géopolitique (Drasah: the ONE lock event).

    A lord's marriage stays silent — the red pseudo is the intel the
    adversaries must observe. A ROYAL wedding (Reine) is a grand event:
    its lock IS announced (Drasah 2026-10-11), with the doubled hours
    and the season-long security.

    Drasah's 2026-10-12 tune: the bride is the PROMISE herself
    (« Thérèse de León, promise de la civilisation Portugais » —
    never a raw civilization name) and the announcement stays
    LIGHT — the details (gains, lock window) live in ONE public
    « En savoir plus » thread attached to the announcement. The
    thread body names the CIVILIZATION alone (Drasah's same-day
    v3 shape: gains in bullet lines, « Durée des Noces » lock
    with the Paris end time, no promise inside).
    """
    if guild is None:
        return
    from kingdoms.mods.kingdoms.diplomacy import _pick_promise
    from kingdoms.mods.kingdoms.kingdom_content import _announce_geopolitics_message
    from kingdoms.mods.kingdoms.kingdom_realm_content import _lock_until_label

    promise = _pick_promise(f"{king_id}:{civ_name}", civ_name)
    couple = (
        str(strings["royal_wedding_couple"]).format(promise, civ_name)
        if promise
        else civ_name
    )
    try:
        message = await _announce_geopolitics_message(
            guild,
            str(strings["royal_wedding_announce"]).format(
                king_name, couple, kingdom_name
            ),
        )
    except Exception:
        logger.warning("KINGDOMS: royal wedding announcement failed", exc_info=True)
        return
    if message is None:
        return
    try:
        create_thread = getattr(message, "create_thread", None)
        if create_thread is None:
            return
        lock_strings = {str(key): str(value) for key, value in strings.items()}
        lock_label = (
            _lock_until_label(int(lock_until), lock_strings)
            if int(lock_until) > 0
            else f"{hours}h"
        )
        # discord.py 2.6 live finding (2026-10-12): channel.send() hands
        # back a PartialMessage whose create_thread() takes NO content
        # kwarg — the TypeError killed the thread silently. Create the
        # thread bare, then send the detail message inside it.
        thread = await create_thread(name=str(strings["royal_wedding_more_title"]))
        thread_send = getattr(thread, "send", None)
        if thread_send is not None:
            await thread_send(
                str(strings["royal_wedding_more_body"]).format(
                    civ_name, kingdom_name, hours, lock_label
                )
            )
    except Exception:
        logger.warning("KINGDOMS: royal wedding thread failed", exc_info=True)


class _ActiveLordsReadError(Exception):
    """The lords list could not be read (wiring or storage failure)."""


async def _member_display_name_safe(guild: discord.Guild | None, user_id: str) -> str | None:
    """Return the member's live display name (None when unresolvable).

    Drasah's rule (2026-10-11): the lord documents used to store a raw
    ``<@id>`` mention as ``display_name`` — every select built from it
    showed a long number instead of a readable pseudo. The live member
    lookup is the reliable source; legacy mention-shaped names are
    rejected by the callers.
    """
    if guild is None or not user_id.isdigit():
        return None
    member = guild.get_member(int(user_id))
    if member is None:
        try:
            member = await guild.fetch_member(int(user_id))
        except Exception:
            return None
    return getattr(member, "display_name", None) or getattr(member, "name", None)


def _lord_label(lord: Any) -> str:
    """One readable select label: live pseudo, else stored, else id."""
    stored = str(getattr(lord, "display_name", "") or "")
    if re.fullmatch(r"<@!?\d+>", stored.strip()):
        stored = ""  # legacy mention-stored name — unreadable in a select
    return (stored or str(lord.id))[:100]


async def _active_lord_options(
    strings: dict[str, str], guild: discord.Guild | None = None
) -> list[discord.SelectOption] | None:
    """List the season's active lords (assigned or waiting) as select options.

    Drasah's rule (2026-10-11): ejecting a player must not require
    typing their handle — the admin picks them from the active roster.
    Returns None when the roster is empty or unreadable.
    """
    wiring = _wiring()
    if wiring.kingdoms_service is None:
        return None
    try:
        lords = [lord for lord in await wiring.kingdoms_service.lords() if not lord.left]
        kingdoms = {kingdom.id: kingdom.name for kingdom in await wiring.kingdoms_service.kingdoms()}
    except Exception as exc:
        logger.warning("KINGDOMS ADMIN: active-lords read failed", exc_info=True)
        raise _ActiveLordsReadError(type(exc).__name__) from exc
    options: list[discord.SelectOption] = []
    for lord in lords:
        if not (lord.kingdom_id or lord.in_queue):
            continue
        kingdom_name = kingdoms.get(lord.kingdom_id or "", None) if lord.kingdom_id else None
        description = kingdom_name or strings["remove_queue_desc"]
        emoji = "👑" if str(lord.role) == "king" else None
        live_name = await _member_display_name_safe(guild, str(lord.id))
        label = (live_name or _lord_label(lord))[:100]
        options.append(
            discord.SelectOption(
                label=label,
                value=str(lord.id)[:100],
                description=str(description)[:100],
                emoji=emoji,
            )
        )
    return options[:25]


class KingdomRemovePlayerView(discord.ui.View):
    """The admin ephemeral roster: pick the active lord to eject."""

    def __init__(self, options: list[discord.SelectOption], strings: dict[str, str]) -> None:
        super().__init__(timeout=300)
        select: discord.ui.Select[Any] = discord.ui.Select(
            custom_id="kingdoms:remove:choose",
            placeholder=strings["remove_select_placeholder"][:100],
            options=options,
            min_values=1,
            max_values=1,
        )

        async def on_choose(target: discord.Interaction[Any]) -> None:
            user_id = (select.values or [""])[0]
            if not user_id:
                return
            # Drasah's rule (2026-10-11): an ejection is destructive —
            # ask for a confirmation before touching anything.
            confirm = discord.ui.View(timeout=300)
            yes: discord.ui.Button[Any] = discord.ui.Button(
                label=strings["remove_confirm_yes"][:80],
                style=discord.ButtonStyle.danger,
                custom_id="kingdoms:remove:confirm",
            )
            no: discord.ui.Button[Any] = discord.ui.Button(
                label=strings["remove_confirm_no"][:80],
                style=discord.ButtonStyle.secondary,
                custom_id="kingdoms:remove:cancel",
            )

            async def on_yes(target_yes: discord.Interaction[Any]) -> None:
                await target_yes.response.defer(ephemeral=True)
                await _revoke_kingdoms_roles(target_yes.guild, user_id)
                await _apply_player_leave_safe(target_yes.guild, user_id, "admin eject", "fr")
                await _log_admin_action_safe(
                    target_yes.guild,
                    f"⚔️ <@{target_yes.user.id}> a éjecté <@{user_id}> de son royaume",
                )
                await target_yes.followup.send(
                    strings["remove_player_done"].format(f"<@{user_id}>"), ephemeral=True
                )

            async def on_no(target_no: discord.Interaction[Any]) -> None:
                await target_no.response.edit_message(content=strings["remove_cancelled"], view=None)

            yes.callback = on_yes  # type: ignore[method-assign, assignment]
            no.callback = on_no  # type: ignore[method-assign, assignment]
            confirm.add_item(yes)
            confirm.add_item(no)
            await target.response.edit_message(
                content=strings["remove_confirm_line"].format(f"<@{user_id}>"), view=confirm
            )

        select.callback = on_choose  # type: ignore[method-assign, assignment]
        self.add_item(select)


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
        await _revoke_kingdoms_roles(interaction.guild, user_id)
        await _apply_player_leave_safe(interaction.guild, user_id, "admin eject", self.locale)
        await _log_admin_action_safe(
            interaction.guild,
            f"⚔️ <@{interaction.user.id}> a éjecté <@{user_id}> de son royaume",
        )
        note = strings["remove_player_done"].format(f"<@{user_id}>")
        await interaction.response.send_message(note, ephemeral=True)


def _profile_strings(locale: str) -> dict[str, str]:
    from kingdoms.mods.kingdoms.kingdom_profiles import _strings as ps

    return ps(locale)


def _king_name_strings(locale: str) -> dict[str, Any]:
    """Return the king-name flow strings from the panels (kingdom_panels).

    Regression (drasah live incident 2026-10-10): the flow first read
    these keys from the profile strings — a silent ``KeyError`` on
    ``king_name_dm_title`` meant the naming DM was never sent at all.
    """
    from kingdoms.mods.kingdoms.kingdom_panels import _strings

    return _strings(locale)


class KingdomAssignPlayerModal(discord.ui.Modal):
    """The admin form to assign one waiting player to a kingdom."""

    player: discord.ui.TextInput[KingdomAssignPlayerModal] = discord.ui.TextInput(
        label="Player (mention or id)",
        placeholder="@player",
        max_length=32,
        required=True,
    )
    kingdom: discord.ui.TextInput[KingdomAssignPlayerModal] = discord.ui.TextInput(
        label="Kingdom name",
        placeholder="Aquitaine",
        max_length=45,
        required=True,
    )

    def __init__(self, locale: str) -> None:
        self.locale = "fr" if str(locale).lower().startswith("fr") else "en"
        strings = _profile_strings(self.locale)
        self.player.label = strings["assign_player_field"][:45]
        self.kingdom.label = strings["assign_kingdom_field"][:45]
        super().__init__(title=strings["assign_modal_title"][:45], timeout=300)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        strings = _profile_strings(self.locale)
        raw = (self.player.value or "").strip()
        match = re.search(r"<@!?(\d+)>", raw) or re.search(r"^(\d{6,25})$", raw)
        if match is None:
            await interaction.response.send_message(strings["remove_player_not_found"], ephemeral=True)
            return
        user_id = match.group(1)
        kingdom_name = (self.kingdom.value or "").strip()
        wiring = _wiring()
        if wiring.kingdoms_service is None:
            await interaction.response.send_message(strings["assign_failed"].format("no service"), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            lord = await wiring.kingdoms_service.assign(user_id, kingdom_name, "lord")
        except Exception as exc:
            await interaction.followup.send(strings["assign_failed"].format(type(exc).__name__), ephemeral=True)
            return
        mention = f"<@{user_id}>"
        await interaction.followup.send(strings["assign_done"].format(mention, lord.kingdom_id), ephemeral=True)
        await _refresh_realm_views_for_kingdom_safe(interaction.guild, str(lord.kingdom_id or ""))


class KingdomAddKingdomModal(discord.ui.Modal):
    """The admin form to add one kingdom manually to the season."""

    kingdom: discord.ui.TextInput[KingdomAddKingdomModal] = discord.ui.TextInput(
        label="Kingdom name",
        placeholder="Aquitaine",
        max_length=45,
        required=True,
    )

    def __init__(self, locale: str) -> None:
        self.locale = "fr" if str(locale).lower().startswith("fr") else "en"
        strings = _profile_strings(self.locale)
        self.kingdom.label = strings["add_kingdom_field"][:45]
        super().__init__(title=strings["add_kingdom_modal_title"][:45], timeout=300)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        strings = _profile_strings(self.locale)
        kingdom_name = (self.kingdom.value or "").strip()
        wiring = _wiring()
        if wiring.kingdoms_service is None:
            await interaction.response.send_message(strings["add_kingdom_failed"].format("no service"), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            kingdom = await wiring.kingdoms_service.add_kingdom(kingdom_name)
        except Exception as exc:
            await interaction.followup.send(strings["add_kingdom_failed"].format(type(exc).__name__), ephemeral=True)
            return
        await interaction.followup.send(strings["add_kingdom_done"].format(kingdom.name), ephemeral=True)
        await _draw_territories_for_kingdom_safe(kingdom.id)
        if interaction.guild is not None:
            await _ensure_realms_after_launch(interaction.guild, self.locale)
            # a kingdom created after the fact opens a throne: the Kings
            # enrolled while nothing existed were stuck (Drasah's
            # 2026-10-11 report) — ping them now with the claim DM.
            await _notify_heirs_safe(interaction.guild, self.locale)


class KingdomLaunchModal(discord.ui.Modal):
    """The admin form to launch a season (no kingdom names — free mode).

    Launching a season never creates kingdoms anymore (Drasah's rule):
    kingdoms exist through a lord's proposal or the admin « add a
    kingdom » button — nothing else.
    """

    def __init__(self, locale: str) -> None:
        self.locale = "fr" if str(locale).lower().startswith("fr") else "en"
        strings = _profile_strings(self.locale)
        super().__init__(title=strings["launch_modal_title"][:45], timeout=300)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        strings = _profile_strings(self.locale)
        wiring = _wiring()
        if wiring.kingdoms_service is None:
            await interaction.response.send_message(strings["launch_failed"].format("no service"), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        # reset-ghost-roles-v1: launch() ALSO wipes the season data —
        # strip the player roles before the documents disappear.
        await _strip_player_mod_roles_for_all_lords_safe(interaction.guild)
        # reset-ghost-roles-v2 (Drasah 29/10): deleting the ROLES heals
        # the ghosts the doc-driven sweep cannot see (blank's Kingdoms
        # King) — launch() is a season wipe like back-to-setup.
        await _delete_season_player_roles_safe(interaction.guild)
        try:
            await wiring.kingdoms_service.launch()
        except Exception as exc:
            await interaction.followup.send(strings["launch_failed"].format(type(exc).__name__), ephemeral=True)
            return
        # reset-ghost-roles-v2: fresh roles + re-gated categories, so the
        # next acceptance grants a working role again.
        await _reprovision_mod_roles_safe(interaction.guild)
        await interaction.followup.send(strings["launch_done_free"], ephemeral=True)
        if interaction.guild is not None:
            await _refresh_season_status_safe(interaction.guild, self.locale)
            await _ensure_realms_after_launch(interaction.guild, self.locale)


class KingdomAdminButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=(
        r"kingdoms:admin:(?P<action>remove|reset|reset-confirm|reset-cancel"
        r"|deploy|deploy-confirm|deploy-cancel|sync|sync-confirm|sync-cancel"
        r"|status|assign|add-kingdom|launch|start-season|start-beta|season-mode"
        r"|back-setup|back-setup-confirm|back-setup-cancel"
        r"|end-season|end-season-confirm|end-season-cancel"
        r"|age-next|age-prev|lords-day-enter|lords-day-exit)"
    ),
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
            "launch": strings["launch_button"],
            "remove": strings["remove_player_button"],
            "reset": strings["reset_salons_button"],
            "reset-confirm": strings["reset_confirm_button"],
            "reset-cancel": strings["reset_cancel_button"],
            "deploy": strings["deploy_button"],
            "deploy-confirm": strings["deploy_confirm_button"],
            "deploy-cancel": strings["deploy_cancel_button"],
            "sync": strings["sync_button"],
            "sync-confirm": strings["sync_confirm_button"],
            "sync-cancel": strings["sync_cancel_button"],
            "status": strings["status_button"],
            "assign": strings["assign_button"],
            "add-kingdom": strings["add_kingdom_button"],
            "start-season": strings["start_season_button"],
            "start-beta": strings["start_beta_button"],
            "season-mode": strings["season_mode_button"],
            "back-setup": strings["back_setup_button"],
            "back-setup-confirm": strings["back_setup_confirm_button"],
            "back-setup-cancel": strings["back_setup_cancel_button"],
            "end-season": strings["end_season_button"],
            "end-season-confirm": strings["end_season_confirm_button"],
            "end-season-cancel": strings["end_season_cancel_button"],
            "age-next": strings["age_next_button"],
            "age-prev": strings["age_prev_button"],
            "lords-day-enter": strings["lords_day_enter_button"],
            "lords-day-exit": strings["lords_day_exit_button"],
        }
        styles = {
            "launch": discord.ButtonStyle.success,
            "remove": discord.ButtonStyle.danger,
            "reset": discord.ButtonStyle.danger,
            "reset-confirm": discord.ButtonStyle.success,
            "reset-cancel": discord.ButtonStyle.secondary,
            "deploy": discord.ButtonStyle.primary,
            "deploy-confirm": discord.ButtonStyle.success,
            "deploy-cancel": discord.ButtonStyle.secondary,
            "sync": discord.ButtonStyle.secondary,
            "sync-confirm": discord.ButtonStyle.success,
            "sync-cancel": discord.ButtonStyle.secondary,
            "status": discord.ButtonStyle.secondary,
            "assign": discord.ButtonStyle.primary,
            "add-kingdom": discord.ButtonStyle.primary,
            "start-season": discord.ButtonStyle.success,
            "start-beta": discord.ButtonStyle.success,
            "season-mode": discord.ButtonStyle.secondary,
            "back-setup": discord.ButtonStyle.danger,
            "back-setup-confirm": discord.ButtonStyle.success,
            "back-setup-cancel": discord.ButtonStyle.secondary,
            "end-season": discord.ButtonStyle.danger,
            "end-season-confirm": discord.ButtonStyle.success,
            "end-season-cancel": discord.ButtonStyle.secondary,
            "age-next": discord.ButtonStyle.success,
            "age-prev": discord.ButtonStyle.secondary,
            "lords-day-enter": discord.ButtonStyle.primary,
            "lords-day-exit": discord.ButtonStyle.secondary,
        }
        return cls(action, labels[action][:80], styles[action])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Run the admin action chosen on the Paramètres panel."""

        async def open_remove(interaction: discord.Interaction, locale: str) -> None:
            """Eject flow: pick from the active roster first, modal as fallback."""
            strings_local = _profile_strings(locale)
            try:
                options = await _active_lord_options(strings_local, interaction.guild)
            except _ActiveLordsReadError as exc:
                await interaction.response.send_message(
                    strings_local["remove_select_failed"].format(str(exc)), ephemeral=True
                )
                return
            if options:
                view = KingdomRemovePlayerView(options, strings_local)
                await interaction.response.send_message(view=view, ephemeral=True)
                return
            if _wiring().kingdoms_service is None:
                await interaction.response.send_modal(KingdomRemovePlayerModal(locale))
                return
            await interaction.response.send_message(strings_local["remove_select_none"], ephemeral=True)

        async def open_assign(_: discord.Interaction, locale: str) -> None:
            await interaction.response.send_modal(KingdomAssignPlayerModal(locale))

        async def open_add_kingdom(_: discord.Interaction, locale: str) -> None:
            await interaction.response.send_modal(KingdomAddKingdomModal(locale))

        locale = str(interaction.locale) if interaction.locale else "en"
        strings = _profile_strings(locale)
        wiring = _wiring()
        if not _is_admin(interaction, wiring.bot_admins):
            await interaction.response.send_message("Only admins can use this panel.", ephemeral=True)
            return
        async def open_launch(_: discord.Interaction, locale: str) -> None:
            await interaction.response.send_modal(KingdomLaunchModal(locale))

        handlers: dict[str, Callable[..., Any]] = {
            "launch": open_launch,
            "remove": open_remove,
            "assign": open_assign,
            "add-kingdom": open_add_kingdom,
            "reset": lambda _i, _l: _ask_action_confirmation(interaction, strings, "reset"),
            "reset-cancel": lambda _i, _l: interaction.response.edit_message(
                content=strings["reset_cancelled"], view=None
            ),
            "deploy": lambda _i, _l: _ask_action_confirmation(interaction, strings, "deploy"),
            "deploy-cancel": lambda _i, _l: interaction.response.edit_message(
                content=strings["deploy_cancelled"], view=None
            ),
            "sync": lambda _i, _l: _ask_action_confirmation(interaction, strings, "sync"),
            "sync-cancel": lambda _i, _l: interaction.response.edit_message(
                content=strings["sync_cancelled"], view=None
            ),
            "status": lambda _i, _l: _run_status(interaction, strings),
            "deploy-confirm": lambda _i, _l: _run_deploy(interaction, strings),
            "sync-confirm": lambda _i, _l: _run_sync(interaction, strings),
            "reset-confirm": lambda _i, _l: _run_reset(interaction, strings),
            "start-season": lambda _i, _l: _run_start_season(interaction, strings),
            "start-beta": lambda _i, _l: _run_start_beta(interaction, strings),
            "season-mode": lambda _i, _l: _run_season_mode(interaction, strings),
            "back-setup": lambda _i, _l: _ask_action_confirmation(interaction, strings, "back-setup"),
            "back-setup-cancel": lambda _i, _l: interaction.response.edit_message(
                content=strings["back_setup_cancelled"], view=None
            ),
            "back-setup-confirm": lambda _i, _l: _run_back_to_setup(interaction, strings),
            "end-season": lambda _i, _l: _ask_action_confirmation(interaction, strings, "end-season"),
            "end-season-cancel": lambda _i, _l: interaction.response.edit_message(
                content=strings["end_season_cancelled"], view=None
            ),
            "end-season-confirm": lambda _i, _l: _run_end_season(interaction, strings),
            "age-next": lambda _i, _l: _run_age_next(interaction, strings),
            "age-prev": lambda _i, _l: _run_age_prev(interaction, strings),
            "lords-day-enter": lambda _i, _l: _run_lords_day_enter(interaction, strings),
            "lords-day-exit": lambda _i, _l: _run_lords_day_exit(interaction, strings),
        }
        handler = handlers.get(self.action)
        if handler is not None:
            await handler(interaction, locale)

    @staticmethod
    def _confirm_button(verdict: str, label: str, style: discord.ButtonStyle) -> KingdomAdminButton:
        return KingdomAdminButton(verdict, label[:80], style)

async def _ask_action_confirmation(interaction: discord.Interaction, strings: dict[str, Any], action: str) -> None:
    """Ask the admin to confirm a destructive/heavy panel action."""
    view = discord.ui.View(timeout=120)
    for verdict, label_key, style in (
        (f"{action}-confirm", f"{action}_confirm_button", discord.ButtonStyle.success),
        (f"{action}-cancel", f"{action}_cancel_button", discord.ButtonStyle.secondary),
    ):
        view.add_item(KingdomAdminButton(verdict, strings[label_key][:80], style))
    await interaction.response.send_message(
        f"**{strings[f'{action}_confirm_title']}**\n{strings[f'{action}_confirm_hint']}",
        view=view,
        ephemeral=True,
    )


async def _run_deploy(interaction: discord.Interaction, strings: dict[str, Any]) -> None:
    """Provision the salons structure then re-pin every panel."""
    from kingdoms.mods.kingdoms.kingdom_panels import deploy_panels
    from kingdoms.mods.kingdoms.kingdom_setup import provision_structure

    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(strings["no_channel"], ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        created, adopted = await provision_structure(guild)
        wiring = _wiring()
        report = await deploy_panels(
            guild,
            wiring.logs_service,
            wiring.bot_admins,
            wiring.mod_roles_service,
            wiring.kingdoms_service,
        )
    except Exception:
        logger.exception("KINGDOMS ADMIN: deployment failed for guild %s", guild.id)
        await interaction.followup.send(strings["reset_failed"], ephemeral=True)
        return
    await _ensure_realms_after_launch(guild, str(interaction.locale) if interaction.locale else "en")
    await interaction.followup.send(
        strings["deploy_done"].format(len(created), len(adopted)) + f" ({', '.join(report) or '—'})",
        ephemeral=True,
    )


async def _run_sync(interaction: discord.Interaction, strings: dict[str, Any]) -> None:
    """Re-pin every panel without touching the salons."""
    from kingdoms.mods.kingdoms.kingdom_panels import deploy_panels

    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(strings["no_channel"], ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    wiring = _wiring()
    try:
        report = await deploy_panels(
            guild,
            wiring.logs_service,
            wiring.bot_admins,
            wiring.mod_roles_service,
            wiring.kingdoms_service,
        )
    except Exception:
        logger.exception("KINGDOMS ADMIN: panel resync failed for guild %s", guild.id)
        await interaction.followup.send(strings["reset_failed"], ephemeral=True)
        return
    if interaction.guild is not None:
        await _repin_realm_views_safe(interaction.guild)
    await interaction.followup.send(strings["sync_done"].format(len(report)), ephemeral=True)


async def _run_start_season(interaction: discord.Interaction, strings: dict[str, Any]) -> None:
    """Start the game: reveal the drafts and draw the territories."""
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(strings["no_channel"], ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    wiring = _wiring()
    if wiring.kingdoms_service is None:
        await interaction.followup.send(strings["start_season_failed"].format("no service"), ephemeral=True)
        return
    try:
        await wiring.kingdoms_service.start_season()
    except Exception as exc:
        await interaction.followup.send(strings["start_season_failed"].format(type(exc).__name__), ephemeral=True)
        return
    locale = str(interaction.locale) if interaction.locale else "en"
    # the reveal: draft starters, territory draw and the real salon views
    await _ensure_realms_after_launch(guild, locale)
    await _refresh_season_status_safe(guild, locale)
    await interaction.followup.send(strings["start_season_done"], ephemeral=True)


async def _run_start_beta(interaction: discord.Interaction, strings: dict[str, Any]) -> None:
    """Arm the beta NOW in ONE click (beta-oneclick-v1, Drasah 27/10).

    The former « Maintenant / Programmer… » confirmation panel is GONE
    — clicking « Démarrer la beta » arms the beta immediately and the
    season starts on its own (beta-auto-season-v1). The click is
    acknowledged silently: the season STATUS message carries the new
    state (no success ephemeral in this salon anymore); errors stay
    ephemeral.
    """
    from kingdoms.mods.kingdoms.beta_panel import (
        _auto_start_season_safe,
        _refresh_taverne_mercenary_safe,
    )
    from kingdoms.mods.kingdoms.game_clock import game_now

    wiring = _wiring()
    events = wiring.events_service
    if events is None:
        await interaction.response.send_message(
            strings["start_beta_failed"].format("no service"), ephemeral=True
        )
        return
    await interaction.response.defer()
    # _auto_start_season_safe reads the beta panel strings (auto_* keys).
    locale = "fr" if str(interaction.locale or "").lower().startswith("fr") else "en"
    from kingdoms.mods.kingdoms.beta_panel import _strings as beta_strings

    beta_s = beta_strings(locale)
    try:
        report = await events.activate_beta("now", start_at=game_now())
    except Exception as exc:
        await interaction.followup.send(
            strings["start_beta_failed"].format(type(exc).__name__), ephemeral=True
        )
        return
    logger.info("KINGDOMS ADMIN: beta armed in one click (kind=%s)", dict(report).get("kind", "?"))
    # beta-auto-season-v1: the season starts on its own — no second
    # click on « Démarrer la saison ». The console shim exposes the
    # live wiring to the BetaStartView-style helpers (§3b click-time
    # resolve) — BOTH services: _auto_start_season_safe reads
    # kingdoms_service through the seam, the previous shim exposed
    # only the events service (the auto-start silently no-op'ed).
    console_shim = SimpleNamespace(
        bot=SimpleNamespace(
            kingdoms_events_service=events,
            kingdoms_service=wiring.kingdoms_service,
        )
    )
    await _auto_start_season_safe(console_shim, interaction, beta_s)
    # mercenary-beta-refresh-v1: the Chief's taverne panel shows the
    # beta call button right after the arm — not at the 20h open.
    await _refresh_taverne_mercenary_safe(interaction)


async def _run_season_mode(interaction: discord.Interaction, strings: dict[str, Any]) -> None:
    """Toggle the imposed/free mode of the running season (no data loss)."""
    await interaction.response.defer(ephemeral=True)
    wiring = _wiring()
    if wiring.kingdoms_service is None:
        await interaction.followup.send(strings["season_mode_failed"].format("no service"), ephemeral=True)
        return
    try:
        season = await wiring.kingdoms_service.current_season()
    except Exception as exc:
        await interaction.followup.send(strings["season_mode_failed"].format(type(exc).__name__), ephemeral=True)
        return
    imposed = bool(getattr(season, "imposed_kingdoms", False)) if season is not None else False
    try:
        await wiring.kingdoms_service.set_imposed_mode(not imposed)
    except Exception as exc:
        await interaction.followup.send(strings["season_mode_failed"].format(type(exc).__name__), ephemeral=True)
        return
    note = strings["season_mode_imposed"] if not imposed else strings["season_mode_free"]
    await interaction.followup.send(note, ephemeral=True)


async def _announce_event_report_safe(guild: discord.Guild, locale: str, report: dict[str, Any]) -> None:
    """Best-effort announcement of one event report in Géopolitique."""
    try:
        from kingdoms.mods.kingdoms.kingdom_content import announce_geopolitics
        from kingdoms.mods.kingdoms.kingdom_events_loop import _report_text, _strings

        await announce_geopolitics(guild, locale, _report_text(_strings(locale), report))
    except Exception:
        logger.info("KINGDOMS ADMIN: event announcement skipped", exc_info=True)


def _events_service_or_fail() -> Any | None:
    """Resolve the event service from the panel wiring."""
    return _wiring().events_service


async def _run_age_next(interaction: discord.Interaction, strings: dict[str, Any]) -> None:
    """Manually run the age switch (Drasah's test seam)."""
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(strings["no_channel"], ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    events = _events_service_or_fail()
    if events is None:
        await interaction.followup.send(strings["age_failed"].format("no service"), ephemeral=True)
        return
    locale = str(interaction.locale) if interaction.locale else "en"
    try:
        report = dict(await events.run_age_switch())
    except Exception as exc:
        await interaction.followup.send(strings["age_failed"].format(type(exc).__name__), ephemeral=True)
        return
    report["kind"] = "age"
    await _announce_event_report_safe(guild, locale, report)
    await _refresh_season_status_safe(guild, locale)
    await interaction.followup.send(
        strings["age_next_done"].format(report.get("display_name", report.get("age", "?"))),
        ephemeral=True,
    )


async def _run_age_prev(interaction: discord.Interaction, strings: dict[str, Any]) -> None:
    """Manually roll back the last age switch (Drasah's test seam)."""
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(strings["no_channel"], ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    events = _events_service_or_fail()
    if events is None:
        await interaction.followup.send(strings["age_failed"].format("no service"), ephemeral=True)
        return
    locale = str(interaction.locale) if interaction.locale else "en"
    try:
        report = dict(await events.rollback_age())
    except Exception as exc:
        await interaction.followup.send(strings["age_prev_failed"].format(type(exc).__name__), ephemeral=True)
        return
    await _refresh_season_status_safe(guild, locale)
    await interaction.followup.send(
        strings["age_prev_done"].format(report.get("display_name", report.get("age", "?"))),
        ephemeral=True,
    )


async def _run_lords_day_enter(interaction: discord.Interaction, strings: dict[str, Any]) -> None:
    """Enter the Lord's Day phase manually: full cycle switch + forced window."""
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(strings["no_channel"], ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    events = _events_service_or_fail()
    if events is None:
        await interaction.followup.send(strings["lords_day_failed"].format("no service"), ephemeral=True)
        return
    locale = str(interaction.locale) if interaction.locale else "en"
    try:
        report = dict(await events.enter_lords_day_phase())
    except Exception as exc:
        await interaction.followup.send(strings["lords_day_failed"].format(type(exc).__name__), ephemeral=True)
        return
    report["kind"] = "cycle"
    await _announce_event_report_safe(guild, locale, report)
    await _refresh_season_status_safe(guild, locale)
    await interaction.followup.send(
        strings["lords_day_enter_done"].format(
            report.get("cycle", "?"), report.get("lords_day_forced_until", "?")
        ),
        ephemeral=True,
    )


async def _run_lords_day_exit(interaction: discord.Interaction, strings: dict[str, Any]) -> None:
    """Close the manually forced Lord's Day window."""
    await interaction.response.defer(ephemeral=True)
    events = _events_service_or_fail()
    if events is None:
        await interaction.followup.send(strings["lords_day_failed"].format("no service"), ephemeral=True)
        return
    try:
        await events.exit_lords_day_phase()
    except Exception as exc:
        await interaction.followup.send(strings["lords_day_failed"].format(type(exc).__name__), ephemeral=True)
        return
    await interaction.followup.send(strings["lords_day_exit_done"], ephemeral=True)


async def _run_status(interaction: discord.Interaction, strings: dict[str, Any]) -> None:
    """Answer with the current season status (kingdoms, queue, players)."""
    wiring = _wiring()
    kingdoms: list[str] = []
    queued = 0
    enrolled = 0
    phase = strings["phase_unknown"]
    mode = strings["mode_free"]
    if wiring.kingdoms_service is not None:
        try:
            all_kingdoms = await wiring.kingdoms_service.kingdoms()
            kingdoms = sorted(k.name for k in all_kingdoms if not k.is_gaia)
            lords = await wiring.kingdoms_service.lords()
            active = [lord for lord in lords if not lord.left]
            queued = sum(1 for lord in active if lord.in_queue)
            enrolled = len(active) - queued
        except Exception:
            logger.warning("KINGDOMS ADMIN: status read failed", exc_info=True)
        try:
            season = await wiring.kingdoms_service.current_season()
            if season is not None:
                phase = strings.get(
                    f"phase_{getattr(season, 'phase', 'started')}", strings["phase_unknown"]
                )
                mode = strings["mode_imposed"] if season.imposed_kingdoms else strings["mode_free"]
        except Exception:
            logger.warning("KINGDOMS ADMIN: season read failed", exc_info=True)
    lines = [
        f"# {strings['status_title']}",
        f"**{strings['status_phase']}** : {phase}",
        f"**{strings['status_mode']}** : {mode}",
        f"**{strings['status_kingdoms']}** : {', '.join(kingdoms) if kingdoms else strings['status_empty']}",
        f"**{strings['status_lords']}** : {enrolled or strings['status_empty']}",
        f"**{strings['status_queue']}** : {queued or strings['status_empty']}",
    ]
    await interaction.response.send_message("\n".join(lines), ephemeral=True)


def _candidature_kingdom_name(content: str, strings: dict[str, Any]) -> str | None:
    """Extract the kingdom name from a candidature message (None when queued)."""
    for line in content.splitlines():
        if strings["queue_value"] in line:
            return None
        if line.startswith(f"**{strings['candidature_kingdom']}** : "):
            value = line.split("** : ", 1)[1].strip() or None
            if value is not None and value == strings["kingdom_after_validation"]:
                return None  # flow v2: the King names the kingdom after approval
            return value
    return None


async def _enroll_applicant(
    kingdoms_service: Any,
    player_id: str,
    *,
    is_king: bool,
    kingdom_name: str | None,
    strings: dict[str, Any],
    display_name: str | None = None,
) -> tuple[str, bool]:
    """Enroll the approved applicant; answer with a note and a success flag.

    ``display_name`` is the live member pseudo (the select menus and the
    announcements read it) — the legacy fallback keeps a mention for the
    announcement rendering, never for a select label.
    """
    from kingdoms.mods.kingdoms.service import (
        KING_ROLE,
        LORD_ROLE,
        AlreadyEnrolledError,
    )

    stored_name = display_name or f"<@{player_id}>"
    if is_king and not kingdom_name:
        try:
            await kingdoms_service.enroll_king_awaiting_kingdom(player_id, stored_name)
        except AlreadyEnrolledError:
            logger.warning("CANDIDATURES: king %s already enrolled", player_id)
            return strings["enroll_already"], False
        except Exception as exc:
            logger.warning("CANDIDATURES: king enrollment failed for %s", player_id, exc_info=True)
            return strings["enroll_failed"].format(type(exc).__name__), False
        season = None
        try:
            season = await kingdoms_service.current_season()
        except Exception:
            season = None
        if season is not None and season.imposed_kingdoms:
            return strings["enroll_king_awaiting_kingdom"], True
        return strings["enroll_king_awaiting_name"], True
    try:
        if is_king:
            lord = await kingdoms_service.enroll(
                player_id, stored_name, KING_ROLE, proposed_name=kingdom_name
            )
        else:
            lord = await kingdoms_service.enroll(
                player_id, stored_name, LORD_ROLE, kingdom_name=kingdom_name
            )
    except AlreadyEnrolledError:
        logger.warning("CANDIDATURES: player %s already enrolled", player_id)
        return strings["enroll_already"], False
    except Exception as exc:
        logger.warning("CANDIDATURES: enrollment failed for %s", player_id, exc_info=True)
        return strings["enroll_failed"].format(type(exc).__name__), False
    if lord.in_queue:
        return strings["enroll_queued"], True
    return strings["enrolled_kingdom"].format(kingdom_name or lord.kingdom_id), True


async def _refresh_season_status_safe(guild: discord.Guild, locale: str) -> None:
    """Best-effort refresh of the season status message (progress included)."""
    try:
        from kingdoms.mods.kingdoms.kingdom_panels import refresh_season_status
        from kingdoms.mods.kingdoms.snapshot import season_label

        wiring = _wiring()
        names: list[str] = []
        queued = 0
        progress = ""
        if wiring.kingdoms_service is not None:
            kingdoms = await wiring.kingdoms_service.kingdoms()
            names = sorted(k.name for k in kingdoms if not k.is_gaia)
            lords = await wiring.kingdoms_service.lords()
            queued = sum(1 for lord in lords if not lord.left and lord.in_queue)
            season = await wiring.kingdoms_service.current_season()
            if season is not None:
                progress = season_label(season, wiring.kingdoms_service.config, locale=locale)
        await refresh_season_status(guild, locale, kingdoms=names, queued=queued, progress=progress)
        # permissions-serveur-v1: the age pin (Géopolitique name emoji
        # + pinned message) follows every mutation — the age switch,
        # the season launches and the machine-à-temps jumps ride the
        # same refresh hook (best effort, never blocks the caller).
        if wiring.kingdoms_service is not None:
            from kingdoms.mods.kingdoms.kingdom_content import refresh_age_pin

            await refresh_age_pin(guild, locale, wiring.kingdoms_service)
    except Exception:
        logger.info("KINGDOMS: season status refresh skipped", exc_info=True)


async def _announce_enrollment_safe(
    guild: discord.Guild,
    locale: str,
    display_name: str,
    kingdom_name: str | None,
    is_king: bool,
) -> None:
    """Best-effort Géopolitique enrollment announcement."""
    try:
        from kingdoms.mods.kingdoms.kingdom_content import announce_enrollment

        if display_name:
            await announce_enrollment(guild, locale, display_name, kingdom_name, is_king)
    except Exception:
        logger.info("KINGDOMS: enrollment announcement skipped", exc_info=True)


async def _send_welcome(
    guild: discord.Guild,
    applicant_id: str,
    strings: dict[str, Any],
) -> None:
    """DM the validated player; fall back to the profile channel if DMs are closed."""
    member = guild.get_member(int(applicant_id))
    if member is None:
        try:
            member = await guild.fetch_member(int(applicant_id))
        except Exception:
            member = None
    if member is None:
        logger.warning("CANDIDATURES: welcome DM skipped — member %s not found", applicant_id)
        return
    welcome = f"# {strings['welcome_title']}\n{strings['welcome_body']}"
    try:
        await member.send(welcome)
    except Exception:
        logger.info("CANDIDATURES: welcome DM failed, falling back to the profile channel")
        from kingdoms.mods.kingdoms.kingdom_profiles import ensure_profile_channel

        channel = await ensure_profile_channel(guild, member)
        if channel is not None:
            await channel.send(f"{welcome}\n{strings['welcome_fallback']}")


async def _send_king_name_request(
    guild: discord.Guild,
    applicant_id: str,
    locale: str,
    available: list[Any] | None = None,
) -> None:
    """DM the approved King asking for their kingdom's name (flow v2, D70).

    Drasah 18/10: when kingless thrones exist (kingdoms added by an
    admin, for instance), the DM ALSO carries the claim select — the
    King may found a new kingdom OR take an existing throne.
    """
    member = guild.get_member(int(applicant_id))
    if member is None:
        try:
            member = await guild.fetch_member(int(applicant_id))
        except Exception:
            member = None
    if member is None:
        logger.warning("CANDIDATURES: king name DM skipped — member %s not found", applicant_id)
        return
    strings = _king_name_strings(locale)
    request = f"# {strings['king_name_dm_title']}\n{strings['king_name_dm_body']}"
    if available:
        request = f"{request}\n\n{strings['king_name_claim_hint']}"
    view = discord.ui.View(timeout=None)
    view.add_item(KingdomKingNameButton(strings["king_name_button"][:80]))
    if available:
        view.add_item(
            KingdomKingClaimSelect(
                [
                    discord.SelectOption(label=str(k.name)[:100], value=str(k.id))
                    for k in available[:25]
                ],
                strings["king_claim_placeholder"][:100],
            )
        )
    try:
        await member.send(request, view=view)
    except Exception:
        logger.info("CANDIDATURES: kingdom-name DM failed, falling back to the profile channel")
        from kingdoms.mods.kingdoms.kingdom_profiles import ensure_profile_channel

        channel = await ensure_profile_channel(guild, member)
        if channel is not None:
            await channel.send(
                f"{request}\n{strings['king_name_dm_fallback']}", view=view
            )


async def _send_king_kingdom_request(guild: discord.Guild, applicant_id: str, locale: str) -> None:
    """Route the approved King to the right follow-up flow.

    Drasah's rule (2026-10-11): in imposed mode the naming DM makes no
    sense — the King picks an existing throne instead (a claim select
    when one is available, an awaiting-kingdom notice otherwise).
    """
    wiring = _wiring()
    if wiring.kingdoms_service is None:
        return
    season = None
    try:
        season = await wiring.kingdoms_service.current_season()
    except Exception:
        season = None
    available: list[Any] = []
    try:
        available = await wiring.kingdoms_service.available_kingdoms()
    except Exception:
        logger.info("CANDIDATURES: available kingdoms lookup failed", exc_info=True)
        available = []
    if season is not None and season.imposed_kingdoms:
        if available:
            await _send_king_claim_request(guild, applicant_id, locale, available)
        else:
            await _send_king_awaiting_kingdom(guild, applicant_id, locale)
        return
    await _send_king_name_request(guild, applicant_id, locale, available)


async def _send_king_claim_request(
    guild: discord.Guild,
    applicant_id: str,
    locale: str,
    kingdoms: list[Any],
) -> None:
    """DM the approved King a select of the available imposed thrones."""
    member = guild.get_member(int(applicant_id))
    if member is None:
        try:
            member = await guild.fetch_member(int(applicant_id))
        except Exception:
            member = None
    if member is None:
        logger.warning("CANDIDATURES: king claim DM skipped — member %s not found", applicant_id)
        return
    strings = _king_name_strings(locale)
    request = f"# {strings['king_claim_dm_title']}\n{strings['king_claim_dm_body']}"
    view = discord.ui.View(timeout=None)
    view.add_item(
        KingdomKingClaimSelect(
            [
                discord.SelectOption(label=str(k.name)[:100], value=str(k.id))
                for k in kingdoms[:25]
            ],
            strings["king_claim_placeholder"][:100],
        )
    )
    try:
        await member.send(request, view=view)
    except Exception:
        logger.info("CANDIDATURES: kingdom-claim DM failed, falling back to the profile channel")
        from kingdoms.mods.kingdoms import kingdom_profiles as _kingdom_profiles

        channel = await _kingdom_profiles.ensure_profile_channel(guild, member)
        if channel is not None:
            await channel.send(
                f"{request}\n{strings['king_name_dm_fallback']}", view=view
            )


async def _send_king_awaiting_kingdom(guild: discord.Guild, applicant_id: str, locale: str) -> None:
    """DM the approved King that no throne is available yet."""
    member = guild.get_member(int(applicant_id))
    if member is None:
        try:
            member = await guild.fetch_member(int(applicant_id))
        except Exception:
            member = None
    if member is None:
        logger.warning(
            "CANDIDATURES: awaiting-kingdom DM skipped — member %s not found", applicant_id
        )
        return
    strings = _king_name_strings(locale)
    notice = f"# {strings['king_awaiting_kingdom_title']}\n{strings['king_awaiting_kingdom_body']}"
    try:
        await member.send(notice)
    except Exception:
        logger.info("CANDIDATURES: awaiting-kingdom DM failed, falling back to the profile channel")
        from kingdoms.mods.kingdoms import kingdom_profiles as _kingdom_profiles

        channel = await _kingdom_profiles.ensure_profile_channel(guild, member)
        if channel is not None:
            await channel.send(notice)


async def _purge_and_reinstall(guild: discord.Guild) -> tuple[bool, int]:
    """Purge every kingdoms salon/category, then re-provision the structure.

    Shared by the salons reset and the Back-to-setup flow: the deleted
    channels carried the pinned panels, so the reinstall re-provisions
    the declared structure and re-pins every panel (kingdoms#138) —
    one click leaves the guild in the fresh, working state.
    """
    deleted = 0
    try:
        wiring = _wiring()
        structure_names = _declared_structure_slugs(getattr(wiring, "registry", None))
        deleted = await _delete_matching_channels(guild, structure_names)
        deleted += await _delete_matching_categories(guild, structure_names)
    except Exception:
        logger.exception("KINGDOMS ADMIN: salons reset failed for guild %s", guild.id)
        return False, deleted
    # reset-visibility-v1 (Drasah 27/10): the purge kills the declared
    # salons, but the member-level overwrites of the survivors (Drasah's
    # hand-made Beta, the taverne zone) and the taverne door role would
    # outlive the season — sweep them BEFORE the reinstall.
    await _strip_member_overwrites_safe(guild)
    reinstalled = False
    try:
        from kingdoms.mods.kingdoms.kingdom_panels import deploy_panels
        from kingdoms.mods.kingdoms.kingdom_setup import provision_structure

        await provision_structure(guild)
        wiring = _wiring()
        await deploy_panels(
            guild,
            wiring.logs_service,
            wiring.bot_admins,
            wiring.mod_roles_service,
            wiring.kingdoms_service,
        )
        reinstalled = True
    except Exception:
        logger.exception("KINGDOMS ADMIN: reinstall after reset failed for guild %s", guild.id)
    return reinstalled, deleted


async def _strip_player_mod_roles_for_all_lords_safe(guild: discord.Guild | None) -> int:
    """Remove the Kingdoms player roles BEFORE a season wipe (reset-ghost-roles-v1).

    Drasah's live finding (2026-10-27): after a back-to-setup reset the
    ex-players kept their kingdoms_lord/kingdoms_king roles — the whole
    channel gating (invariant nouvel arrivant, immersion-v1) is carried
    by those roles, so they still saw every game salon (and NOT
    Beta/Conscription, denied to the roles). Same doc-driven class as
    the ghost 💍: once the lord documents are wiped nothing ever
    iterates the members again. This sweep runs BEFORE the wipe (while
    the documents still resolve the members) and ALSO reads the season
    archives, healing the ghosts of previously wiped seasons whose
    documents are already gone. Best-effort and idempotent: one
    failed removal never blocks the others. Returns the number of
    distinct members actually resolved.
    """
    if guild is None:
        return 0
    wiring = _wiring()
    service = wiring.kingdoms_service
    member_ids: set[str] = set()
    try:
        for lord in await service.lords() if service is not None else []:
            member_ids.add(str(getattr(lord, "id", "")))
    except Exception:
        logger.warning("KINGDOMS: lord list read failed (player role strip)", exc_info=True)
    try:
        for archive in await service.season_archives() if service is not None else []:
            for doc in archive.get("lords", []) or []:
                if isinstance(doc, dict):
                    raw = str(doc.get("_id", ""))
                    if raw:
                        member_ids.add(raw)
    except Exception:
        logger.warning("KINGDOMS: season archive read failed (player role strip)", exc_info=True)
    from kingdoms.mods.kingdoms.kingdom_panels import KINGDOM_MOD, ROLE_KING, ROLE_LORD

    roles: list[discord.Role] = []
    for role_key, display_name in (
        (ROLE_LORD, "Kingdoms Lord"),
        (ROLE_KING, "Kingdoms King"),
    ):
        role: discord.Role | None = None
        if wiring.mod_roles_service is not None:
            try:
                role_id = await wiring.mod_roles_service.resolve_role_id(
                    str(guild.id), KINGDOM_MOD, role_key
                )
                if role_id is not None and role_id.isdigit():
                    role = guild.get_role(int(role_id))
            except Exception:
                logger.warning(
                    "KINGDOMS: mod role resolution failed (%s)", role_key, exc_info=True
                )
        if role is None:
            role = discord.utils.get(guild.roles, name=display_name)
        if role is not None:
            roles.append(role)
    if not roles:
        logger.warning("KINGDOMS: player role strip found no Lord/King roles in guild %s", guild.id)
        return 0
    resolved = 0
    for user_id in sorted(member_ids):
        if not user_id.isdigit():
            continue
        member = await _resolve_member(guild, int(user_id))
        if member is None:
            continue
        resolved += 1
        try:
            await member.remove_roles(
                *roles, reason="kingdoms: wipe saison — rôles joueurs retirés"
            )
        except Exception:
            logger.warning(
                "KINGDOMS: player role strip failed for %s", user_id, exc_info=True
            )
    if resolved:
        logger.info(
            "KINGDOMS: reset-ghost-roles-v1 — %s member(s) stripped of Kingdoms Lord/King (guild %s)",
            resolved,
            guild.id,
        )
    return resolved


async def _delete_season_player_roles_safe(guild: discord.Guild | None) -> int:
    """Delete the season player ROLES before a wipe (reset-ghost-roles-v2).

    Drasah's live finding (2026-10-29): blank — an ex-KING of a previous
    season — kept his Kingdoms King role after a back-to-setup reset,
    so he still saw every game salon. The v1 sweep is doc-driven
    (current lords + season archives): a ghost whose documents AND
    archives no longer resolve him is invisible to it forever. Deleting
    the ROLES — the pattern already proven by the taverne door role
    (reset-visibility-v1) — strips every holder at once, independent of
    the members intent and of any document. The role MAPPINGS die with
    them (a stale mapping would pin a dead role id and ``_resolve_or_
    provision`` never re-checks the platform); the wipe flow recreates
    fresh roles via ``_reprovision_mod_roles_safe`` right after the
    data wipe. ``kingdoms_admin`` is a staff role: it survives.
    """
    if guild is None:
        return 0
    from kingdoms.mods.kingdoms.kingdom_panels import KINGDOM_MOD, ROLE_KING, ROLE_LORD

    wiring = _wiring()
    service = wiring.mod_roles_service
    deleted = 0
    for role_key, display_name in (
        (ROLE_LORD, "Kingdoms Lord"),
        (ROLE_KING, "Kingdoms King"),
        ("kingdoms_locked", MARRIAGE_LOCK_ROLE_NAME),
    ):
        role: discord.Role | None = None
        if service is not None:
            try:
                role_id = await service.resolve_role_id(str(guild.id), KINGDOM_MOD, role_key)
                if role_id is not None and role_id.isdigit():
                    role = guild.get_role(int(role_id))
            except Exception:
                logger.warning(
                    "KINGDOMS: mod role resolution failed (%s, role delete)", role_key, exc_info=True
                )
        if role is None:
            role = discord.utils.get(getattr(guild, "roles", []) or [], name=display_name)
        if role is not None:
            try:
                await role.delete(
                    reason="kingdoms: wipe saison — rôle joueur supprimé (reset-ghost-roles-v2)"
                )
                deleted += 1
            except Exception:
                logger.warning("KINGDOMS: player role delete failed (%s)", role_key, exc_info=True)
        if service is not None:
            await service.delete_role_mapping(str(guild.id), KINGDOM_MOD, role_key)
    if deleted:
        logger.info(
            "KINGDOMS: reset-ghost-roles-v2 — %s season player role(s) deleted (guild %s)",
            deleted,
            guild.id,
        )
    return deleted


async def _reprovision_mod_roles_safe(guild: discord.Guild | None) -> None:
    """Recreate the deleted player roles and re-gate the categories (v2).

    ``_delete_season_player_roles_safe`` dropped the roles AND their
    mappings: ``setup_mod_roles`` recreates the missing ones and
    persists fresh ids, then the player category gating pass re-writes
    the overwrites so the fresh roles (new ids) unlock the categories
    again — without this, nobody could be granted a working view until
    the next boot provisioning. Best effort, idempotent.
    """
    if guild is None:
        return
    from kingdoms.mods.kingdoms.kingdom_panels import KINGDOM_MOD

    wiring = _wiring()
    if wiring.mod_roles_service is not None:
        try:
            await wiring.mod_roles_service.setup_mod_roles(str(guild.id), KINGDOM_MOD)
            logger.info("KINGDOMS: reset-ghost-roles-v2 — player roles re-provisioned (guild %s)", guild.id)
        except Exception:
            logger.warning("KINGDOMS: player role re-provision failed", exc_info=True)
    from kingdoms.mods.kingdoms.kingdom_setup import _enforce_player_category_gating

    try:
        await _enforce_player_category_gating(guild)
    except Exception:
        logger.warning("KINGDOMS: player category re-gating failed", exc_info=True)


def _member_overwrite_ids(target: object, bot_id: int | None) -> list[int]:
    """The member-level overwrite user ids recorded on one target (duck-typed).

    reset-visibility-v1 (Drasah 27/10): the same helper reads the ids
    from every storage shape the codebase meets — the discord.py
    ``overwrites`` view (resolved Members, or unresolved
    ``Object(type=User)`` since the bot runs WITHOUT the members
    intent), the mock tuple-keyed maps (``(id, is_role)`` in
    ``_permissions``/``_overwrites``) and the prod raw overwrite list
    (``.is_member()``/``.id``). The bot's own id is never returned.
    """
    ids: list[int] = []

    def _record(user_id: object) -> None:
        try:
            resolved = int(user_id)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return
        if resolved and resolved != bot_id and resolved not in ids:
            ids.append(resolved)

    view = getattr(target, "overwrites", None)
    if view:
        try:
            for key, overwrite in list(view.items()):  # type: ignore[union-attr]
                if overwrite is None:
                    continue
                if isinstance(key, discord.Member):
                    _record(key.id)
                elif isinstance(key, discord.Object) and getattr(key, "type", None) is discord.User:
                    _record(key.id)
        except Exception:
            pass
    for raw in (getattr(target, "_permissions", None), getattr(target, "_overwrites", None)):
        if not raw:
            continue
        if isinstance(raw, dict):
            for key, overwrite in raw.items():  # type: ignore[union-attr]
                if overwrite is None:
                    continue
                if isinstance(key, tuple) and len(key) == 2 and key[1] is False:
                    _record(key[0])
        else:
            for entry in raw:  # type: ignore[union-attr]
                try:
                    if entry.is_member():
                        _record(entry.id)
                except Exception:
                    continue
    return ids


async def _strip_member_overwrites_safe(guild: discord.Guild | None) -> int:
    """Wipe every MEMBER-level overwrite + the taverne door role at reset.

    reset-visibility-v1 (Drasah 27/10): the taverne immersion poses
    member-level view DENIES on every other category (Beta included)
    and every un-synced channel — a member who never clicked Sortir
    (crash, mid-season restart, or a reset while inside) kept them
    forever: the provisioning self-heal iterates ``guild.members``,
    EMPTY without the members intent, so nothing ever cleaned them
    (blank could not see Beta — his name sat in the overwrites of the
    hidden zones, « idem pour le rôle dans la taverne »). A season
    reset must return EVERY member to the newcomer state, so the wipe
    sweeps the overwrites by id (no member resolution needed).

    The taverne door role (« 🍺 Dans la Taverne ») is DELETED: with no
    members intent the role cannot be enumerated per member — dropping
    the role strips it from everyone at once, and the taverne
    provisioning recreates it fresh (no member holds it → the
    self-heal re-applies no deny). Best effort, idempotent, the bot's
    own overwrite is NEVER touched. Returns the number of strips.
    """
    if guild is None:
        return 0
    bot_id = getattr(getattr(guild, "me", None), "id", None)
    targets: list[object] = [
        *(list(getattr(guild, "categories", []) or [])),
        *(list(getattr(guild, "text_channels", []) or [])),
        *(list(getattr(guild, "voice_channels", []) or [])),
        *(list(getattr(guild, "forums", []) or [])),
    ]
    cleared = 0
    for target in targets:
        for user_id in _member_overwrite_ids(target, bot_id):
            try:
                await target.set_permissions(  # type: ignore[union-attr]
                    discord.Object(id=user_id, type=discord.User), overwrite=None
                )
                cleared += 1
            except Exception:
                logger.warning(
                    "KINGDOMS: member overwrite strip failed on %r for %s",
                    str(getattr(target, "name", "?")),
                    user_id,
                    exc_info=True,
                )
    try:
        from kingdoms.mods.kingdoms.tavern_immersion import TAVERN_ROLE_NAME

        door_role = discord.utils.get(getattr(guild, "roles", []) or [], name=TAVERN_ROLE_NAME)
        if door_role is not None:
            await door_role.delete(reason="kingdoms: reset saison — rôle Taverne retiré")  # type: ignore[union-attr]
            cleared += 1
            logger.info("KINGDOMS: reset-visibility-v1 — taverne door role deleted")
    except Exception:
        logger.warning("KINGDOMS: taverne door role delete failed", exc_info=True)
    if cleared:
        logger.info(
            "KINGDOMS: reset-visibility-v1 — %s member overwrite(s)/door role stripped (guild %s)",
            cleared,
            guild.id,
        )
    return cleared


async def _strip_marriage_livery_for_all_lords_safe(guild: discord.Guild | None) -> int:
    """Remove every marriage livery BEFORE a season wipe (ghost guard).

    Drasah's live finding (2026-10-12): the Retour setup wiped the lord
    DOCUMENTS but the sync pass is doc-driven (no members intent — the
    gateway cache is empty) — once the docs are gone nothing iterates
    the members, so the 💍 pseudo suffix (and the red role) survived
    the wipe forever. The wipe flows call this while the docs still
    exist: each lord's roles AND nickname are cleaned via the same
    cache-then-REST resolution the sync pass uses. Returns the number
    of members actually found.
    """
    if guild is None:
        return 0
    wiring = _wiring()
    service = wiring.kingdoms_service
    try:
        lords = await service.lords() if service is not None else []
    except Exception:
        logger.warning("KINGDOMS: lord list read failed (livery strip)", exc_info=True)
        lords = []
    lock_role = discord.utils.get(guild.roles, name=MARRIAGE_LOCK_ROLE_NAME)
    ring_role = discord.utils.get(guild.roles, name=MARRIAGE_RING_ROLE_NAME)
    found = 0
    seen: set[str] = set()
    for lord in lords:
        lord_id = str(getattr(lord, "id", ""))
        if not lord_id.isdigit() or lord_id in seen:
            continue
        seen.add(lord_id)
        member = await _resolve_member(guild, int(lord_id))
        if member is None:
            continue
        found += 1
        for role in (lock_role, ring_role):
            if role is not None and role in getattr(member, "roles", ()):
                try:
                    await member.remove_roles(
                        role, reason="kingdoms: wipe saison — livery retirée"
                    )
                except Exception:
                    logger.warning(
                        "KINGDOMS: livery role strip failed", exc_info=True
                    )
        await _sync_member_marriage_nickname(member, None)
    return found


async def _run_back_to_setup(interaction: discord.Interaction, strings: dict[str, Any]) -> None:
    """Full test reset: wipe + fresh season in setup, salons rebuilt.

    Drasah's test loop (2026-10-11): the salons-only reset left the
    season data alive, so the reinstalled views reposted the old
    draws. Back-to-setup archives and wipes the season data, relaunches
    a fresh season in ``setup`` (free mode), then rebuilds the salons —
    placeholders back, ready to test again from the beginning.
    """
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(strings["no_channel"], ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    wiring = _wiring()
    if wiring.kingdoms_service is None:
        await interaction.followup.send(strings["back_setup_failed"].format("no service"), ephemeral=True)
        return
    try:
        # reset-ghost-roles-v2 (Drasah 29/10): blank — an ex-king of a
        # previous season — kept his Kingdoms King after the reset: the
        # v1 doc-driven sweep is blind to ghosts whose docs AND archives
        # no longer resolve them. The season player ROLES are DELETED
        # (taverne door pattern): every holder is stripped at once. The
        # mappings die too, and the fresh roles come back below.
        await _delete_season_player_roles_safe(guild)
        # BEFORE the wipe: the sync pass is doc-driven, once the lord
        # documents are gone a stale 💍 pseudo (or red role) would live
        # forever — strip the livery while the docs still resolve.
        await _strip_marriage_livery_for_all_lords_safe(guild)
        # reset-ghost-roles-v1 (Drasah 27/10): the wipe erases the lord
        # documents — the roles must die BEFORE, while the docs still
        # resolve the members (the archives heal the older ghosts).
        await _strip_player_mod_roles_for_all_lords_safe(guild)
        await wiring.kingdoms_service.reset()
        await wiring.kingdoms_service.launch()
        # reset-ghost-roles-v2: the fresh roles must exist BEFORE the
        # reinstall — the category gating pass resolves them by name to
        # write the view overwrites, and acceptance needs a live role.
        await _reprovision_mod_roles_safe(guild)
    except Exception as exc:
        logger.exception("KINGDOMS ADMIN: back-to-setup data wipe failed for guild %s", guild.id)
        await interaction.followup.send(
            strings["back_setup_failed"].format(type(exc).__name__), ephemeral=True
        )
        return
    reinstalled, deleted = await _purge_and_reinstall(guild)
    if reinstalled:
        await interaction.followup.send(
            strings["back_setup_done"].format(deleted), ephemeral=True
        )
        await _refresh_season_status_safe(guild, str(interaction.locale) if interaction.locale else "en")
    else:
        await interaction.followup.send(strings["reset_failed"], ephemeral=True)


async def _run_end_season(interaction: discord.Interaction, strings: dict[str, Any]) -> None:
    """Close the season: territory counts decide, the phase moves to ended.

    Drasah's phase-test rule (2026-10-11): the admin walks the phase
    machine without waiting for the real calendar end — the closing
    runs the Conquest verdict (winner by territory counts, ShowMatch
    PA2 on a tie) and the season lands in ``ended``.
    """
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(strings["no_channel"], ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    wiring = _wiring()
    if wiring.kingdoms_service is None:
        await interaction.followup.send(strings["end_season_failed"].format("no service"), ephemeral=True)
        return
    winner: str | None = None
    try:
        territories = wiring.territories_service
        if territories is not None:
            from kingdoms.mods.kingdoms.season_end import SeasonEndService

            end_service = SeasonEndService(
                wiring.kingdoms_service._store,
                wiring.kingdoms_service.config,
                wiring.kingdoms_service,
                territories,
            )
            report = await end_service.close_season()
            raw_winner = report.get("winner_name") if isinstance(report, dict) else None
            winner = str(raw_winner) if raw_winner else None
        await wiring.kingdoms_service.set_phase("ended")
    except Exception as exc:
        logger.exception("KINGDOMS ADMIN: end-season failed for guild %s", guild.id)
        await interaction.followup.send(
            strings["end_season_failed"].format(type(exc).__name__), ephemeral=True
        )
        return
    if winner:
        await interaction.followup.send(strings["end_season_done"].format(winner), ephemeral=True)
    else:
        await interaction.followup.send(strings["end_season_done_no_winner"], ephemeral=True)
    await _refresh_season_status_safe(guild, str(interaction.locale) if interaction.locale else "en")


async def _run_reset(interaction: discord.Interaction, strings: dict[str, Any]) -> None:
    """Delete every kingdoms channel/category, then report."""
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(strings["no_channel"], ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    reinstalled, deleted = await _purge_and_reinstall(guild)
    if reinstalled:
        # the reset wiped the per-realm salon views with the salons:
        # re-provision every approved kingdom's structure AND content
        # (phase-aware: placeholders stay placeholders in setup) —
        # Drasah's live incident 2026-10-10: the salons came back empty
        # because nothing re-triggered the content deployments.
        try:
            await _ensure_realms_after_launch(
                guild, str(interaction.locale) if interaction.locale else "en"
            )
        except Exception:
            logger.exception("KINGDOMS ADMIN: realm content reinstall failed for guild %s", guild.id)
        await interaction.followup.send(strings["reset_done"].format(deleted), ephemeral=True)
    else:
        await interaction.followup.send(strings["reset_failed"], ephemeral=True)


def _declared_structure_slugs(registry: Any) -> set[str]:
    """Slugs of every declared group and category name; empty set when unknown."""
    from kingdoms.mods.kingdoms.kingdom_setup import MOD_NAME, _slug

    names: set[str] = set()
    if registry is None:
        return names
    try:
        mod = registry.require(MOD_NAME)
    except Exception:
        logger.warning("KINGDOMS ADMIN: mod registry lookup failed", exc_info=True)
        return names
    for group in mod.channel_groups:
        names.add(_slug(group.display_name))
    for category in mod.channel_categories:
        names.add(_slug(category.display_name))
    return names


async def _delete_matching_channels(guild: discord.Guild, structure_names: set[str]) -> int:
    """Delete the structure channels, the profile channels and the epoch members.

    Drasah's live incident (2026-10-11): deleting a category on Discord
    does NOT delete its channels — they become orphan top-level salons
    and the reinstall then duplicates them. The per-kingdom realm
    salons are therefore deleted HERE, before their category goes, and
    the root-level leftovers of older resets are swept too (their slug
    matches a realm salon name and they live outside any category).
    """
    from kingdoms.mods.kingdoms.kingdom_realms import REALM_SALONS
    from kingdoms.mods.kingdoms.kingdom_setup import _slug

    realm_slugs = {_slug(name) for _key, name in REALM_SALONS}
    deleted = 0
    channels = [*list(guild.text_channels), *list(getattr(guild, "forums", []))]
    for channel in channels:
        category = getattr(channel, "category", None)
        in_epoch = (
            category is not None and _slug(getattr(category, "name", "")) == _slug("Époque")
        )
        # ordre-salons-v1 (Drasah 19/10): the realm categories now read
        # « 🏰 Nom » — the detection keeps the legacy « royaume- » slug
        # AND matches the charte's 🏰 prefix (the declared structure
        # categories are caught by structure_names below either way).
        in_realm = (
            category is not None
            and (
                _slug(getattr(category, "name", "")).startswith("royaume-")
                or str(getattr(category, "name", "")).startswith("🏰 ")
            )
        )
        # realm salons die with their kingdom; orphans at the guild root
        # (leftovers of an older reset) match a realm salon slug
        realm_leftover = (
            category is None and _slug(channel.name) in realm_slugs
        )
        name = _slug(channel.name)
        # ordre-salons-v1: the decorative dividers (« ┈┈ ✦ ┈┈ ») slug to
        # the empty string — sweep them so a reinstall never duplicates
        # them or leaves them orphaned at the guild root.
        divider = category is None and name == ""
        if (
            divider
            or in_epoch
            or in_realm
            or realm_leftover
            or (not in_realm and (name in structure_names or name.startswith("profil-")))
        ):
            try:
                await channel.delete()
                deleted += 1
            except Exception:
                logger.warning("KINGDOMS ADMIN: channel delete failed", exc_info=True)
    return deleted


async def _delete_matching_categories(guild: discord.Guild, structure_names: set[str]) -> int:
    """Delete the declared structure categories and the per-kingdom realms.

    The per-kingdom ``Royaume [Nom]`` categories are not declared in the
    YAML — a reset must still purge them, otherwise the reinstall adopts
    the half-deleted slices and the guild ends up with leftovers. The
    approved kingdoms are re-provisioned by the post-reset panel deploy.
    """
    from kingdoms.mods.kingdoms.kingdom_realms import grant_bot_access_to_category
    from kingdoms.mods.kingdoms.kingdom_setup import _slug

    deleted = 0
    for category in list(getattr(guild, "categories", [])):
        slug = _slug(category.name)
        raw_name = str(getattr(category, "name", ""))
        # ordre-salons-v1 (Drasah 19/10): the realm categories now read
        # « 🏰 Nom » — keep sweeping the legacy « royaume- » slugs AND
        # the charte 🏰 prefix (a reset purges the whole structure).
        is_match = slug in structure_names or slug.startswith("royaume-")
        if not is_match and raw_name.startswith("🏰 "):
            is_match = True
        if is_match:
            try:
                await grant_bot_access_to_category(guild, category)
                # deleting a category leaves its channels orphaned at the
                # guild root (they are NOT deleted with it) — remove them
                # first so nothing survives to be duplicated
                for channel in list(getattr(category, "channels", [])):
                    try:
                        await channel.delete()
                        deleted += 1
                    except Exception:
                        logger.warning("KINGDOMS ADMIN: realm salon delete failed", exc_info=True)
                await category.delete()
                deleted += 1
            except Exception:
                logger.warning("KINGDOMS ADMIN: category delete failed", exc_info=True)
    return deleted


async def _refresh_realms_panel_safe(guild: discord.Guild, locale: str) -> None:
    """Best-effort refresh of the Royaumes panel (validation states)."""
    try:
        from kingdoms.mods.kingdoms.kingdom_realms import deploy_realms_panel

        wiring = _wiring()
        await deploy_realms_panel(guild, locale, wiring.kingdoms_service)
    except Exception:
        logger.warning("KINGDOM REALMS: panel refresh failed", exc_info=True)


async def _season_phase_safe() -> str:
    """Return the current season phase ('started' when unknown/legacy)."""
    wiring = _wiring()
    if wiring.kingdoms_service is None:
        return "started"
    try:
        season = await wiring.kingdoms_service.current_season()
    except Exception:
        return "started"
    if season is None:
        return "started"
    return str(getattr(season, "phase", "started") or "started")


async def _draw_territories_after_launch_safe() -> None:
    """Best-effort season initial draw (guarded, never duplicates).

    Skipped while the season is in ``setup`` (Drasah's phase rule): the
    territory distribution only happens when the game starts.
    """
    wiring = _wiring()
    if wiring.territories_service is None:
        return
    if await _season_phase_safe() == "setup":
        logger.info("KINGDOMS: territory draw deferred — season is in setup phase")
        return
    try:
        if await wiring.territories_service.territories():
            logger.info("KINGDOMS: initial territory draw skipped — territories exist")
            return
        await wiring.territories_service.draw_initial()
    except Exception:
        logger.warning("KINGDOMS: initial territory draw failed", exc_info=True)


async def _repin_realm_views_safe(guild: discord.Guild) -> None:
    """Re-pin every approved kingdom's salon state views (salons untouched)."""
    wiring = _wiring()
    if wiring.kingdoms_service is None:
        return
    try:
        kingdoms = await wiring.kingdoms_service.kingdoms()
    except Exception:
        logger.warning("KINGDOM REALM CONTENT: kingdom list read failed", exc_info=True)
        return
    for kingdom in kingdoms:
        # Gaïa included: its Patrouille/Territoire state views are part
        # of the anti-stale refresh (no Alliances view — no civs, ever).
        if str(kingdom.validation) == "approved":
            await _deploy_realm_content_safe(guild, kingdom)


async def _deploy_realm_content_safe(guild: discord.Guild, kingdom: Any) -> None:
    """Best-effort deployment of one kingdom's salon state views (v1)."""
    try:
        from kingdoms.mods.kingdoms.kingdom_realm_content import deploy_realm_content

        await deploy_realm_content(guild, kingdom, _wiring())
    except Exception:
        logger.warning("KINGDOM REALM CONTENT: deployment failed", exc_info=True)


async def _refresh_realm_views_for_kingdom_safe(guild: discord.Guild | None, kingdom_id: str) -> None:
    """Live-refresh one kingdom's state views after a state change (D70 v1).

    Every action that changes a kingdom's state (purchase, marriage,
    enrollment, corruption…) re-renders its salons in place — the state
    views never go stale again.
    """
    wiring = _wiring()
    if guild is None or wiring.kingdoms_service is None or not kingdom_id:
        return
    try:
        kingdoms = await wiring.kingdoms_service.kingdoms()
    except Exception:
        logger.warning("KINGDOM REALM CONTENT: kingdom list read failed", exc_info=True)
        return
    kingdom = next((k for k in kingdoms if str(k.id) == str(kingdom_id)), None)
    if kingdom is not None:
        await _deploy_realm_content_safe(guild, kingdom)


async def _refresh_all_realm_views_safe(guild: discord.Guild | None, locale: str) -> None:
    """Re-render every state view: realm salons, Royaumes panel, season status.

    Drasah's rule (2026-10-11): no view may go stale — after any state
    change the surface shows the live documents. Used by the scheduler
    (cycle/age switches move wallets, territories and budgets without
    any click) and after a leave/eject.
    """
    if guild is None:
        return
    await _repin_realm_views_safe(guild)
    # restructuration-serveur-v1 c (Drasah 19/10): the Gaïa Territoire
    # view carries the LIVE AI level — an age switch moves it with no
    # click, so the Gaïa view re-renders with the same rule.
    try:
        from kingdoms.mods.kingdoms.kingdom_realm_content import deploy_gaia_content

        await deploy_gaia_content(guild, _wiring(), locale)
    except Exception:
        logger.warning("KINGDOM PERSISTENT: gaia view refresh failed", exc_info=True)
    await _refresh_realms_panel_safe(guild, locale)
    await _refresh_season_status_safe(guild, locale)


async def _notify_heirs_safe(guild: discord.Guild | None, locale: str) -> None:
    """DM the queued Kings a claim select when thrones open (Héritiers du trône).

    The claim DM was only sent at the King's validation — a throne
    freed later (leave, eject) never re-triggered it, so the feature
    looked dead. Now any throne opening pings the waiting heirs.
    """
    if guild is None:
        return
    wiring = _wiring()
    if wiring.kingdoms_service is None:
        return
    try:
        available = await wiring.kingdoms_service.available_kingdoms()
    except Exception:
        logger.warning("CANDIDATURES: available kingdoms lookup failed", exc_info=True)
        return
    if not available:
        return
    try:
        lords = await wiring.kingdoms_service.lords()
    except Exception:
        logger.warning("KINGDOMS: lord list read failed", exc_info=True)
        return
    heirs = [
        lord
        for lord in lords
        if not lord.left and lord.in_queue and lord.kingdom_id is None and str(lord.role) == "king"
    ]
    for heir in heirs:
        await _send_king_claim_request(guild, str(heir.id), locale, available)


async def _remove_player_mod_roles_safe(guild: discord.Guild | None, user_id: str) -> None:
    """Remove the Kingdoms Lord/King roles of ONE player (best effort).

    ejection-cleanup-v1 (Drasah 30/10): the channel gating rides the
    player roles — an ejected/departed player who keeps Kingdoms Lord
    still sees every game salon (the reset-ghost-roles-v1 live finding,
    at the single-player scale). The Kingdoms Admin role is NEVER
    touched: it is a staff role, not a game state.
    """
    if guild is None or not user_id.isdigit():
        return
    wiring = _wiring()
    member = await _resolve_member(guild, int(user_id))
    if member is None:
        return
    from kingdoms.mods.kingdoms.kingdom_panels import KINGDOM_MOD, ROLE_KING, ROLE_LORD

    roles: list[discord.Role] = []
    for role_key, display_name in (
        (ROLE_LORD, "Kingdoms Lord"),
        (ROLE_KING, "Kingdoms King"),
    ):
        role: discord.Role | None = None
        if wiring.mod_roles_service is not None:
            try:
                role_id = await wiring.mod_roles_service.resolve_role_id(
                    str(guild.id), KINGDOM_MOD, role_key
                )
                if role_id is not None and str(role_id).isdigit():
                    role = guild.get_role(int(role_id))
            except Exception:
                logger.warning(
                    "KINGDOMS: mod role resolution failed (%s)", role_key, exc_info=True
                )
        if role is None:
            role = discord.utils.get(getattr(guild, "roles", []) or [], name=display_name)
        if role is not None:
            roles.append(role)
    if not roles:
        return
    held = [role for role in roles if role in getattr(member, "roles", ())]
    if not held:
        return
    try:
        await member.remove_roles(
            *held, reason="kingdoms: départ/éjection — rôles joueurs retirés"
        )
        logger.info(
            "KINGDOMS: ejection-cleanup-v1 — player roles stripped for %s (guild %s)",
            user_id,
            guild.id,
        )
    except Exception:
        logger.warning(
            "KINGDOMS: player role strip failed for %s", user_id, exc_info=True
        )


async def _cleanup_departed_player_safe(
    guild: discord.Guild | None, user_id: str, kingdom_id: str
) -> None:
    """Return a departed/ejected player to the NEWCOMER state.

    ejection-cleanup-v1 (Drasah 30/10, beta report): an ejected lord
    kept his member overwrite on the ex-kingdom's category — the realm
    re-syncs are replace-edits that only run on launch/approval, so
    nothing ever removed it and he still read AEGIS while ruling his
    own new kingdom. The leave seam now strips (1) the departed member
    overwrites on the ex-kingdom's category and its salons (by id, no
    member resolution — the bot runs without the members intent) and
    (2) the player's Kingdoms Lord/King roles. Idempotent, best
    effort, the bot's own allow is never touched.
    """
    if guild is None or not user_id:
        return
    wiring = _wiring()
    kingdom = None
    if kingdom_id:
        kingdom = await _kingdom_by_id_safe(wiring.kingdoms_service, kingdom_id)
    if kingdom is not None:
        try:
            from kingdoms.mods.kingdoms.kingdom_realms import _find_realm_category

            category = _find_realm_category(guild, str(getattr(kingdom, "name", "")))
            if category is not None:
                bot_id = getattr(getattr(guild, "me", None), "id", None)
                channels = [category, *(getattr(category, "channels", []) or [])]
                for channel in channels:
                    for overwrite_id in _member_overwrite_ids(channel, bot_id):
                        if str(overwrite_id) != str(user_id):
                            continue
                        try:
                            await channel.set_permissions(
                                discord.Object(id=overwrite_id, type=discord.User),
                                overwrite=None,
                            )
                        except Exception:
                            logger.warning(
                                "KINGDOMS: departed overwrite strip failed for %s on %r",
                                user_id,
                                str(getattr(channel, "name", "?")),
                                exc_info=True,
                            )
        except Exception:
            logger.warning(
                "KINGDOMS: departed overwrite cleanup failed for %s",
                user_id,
                exc_info=True,
            )
    await _remove_player_mod_roles_safe(guild, user_id)


async def _apply_player_leave_safe(
    guild: discord.Guild | None, user_id: str, reason: str, locale: str
) -> bool:
    """Mark the player as left in the season data, refresh the views, ping heirs.

    The leave request flow only annotated the admin message — the data
    never recorded the departure, so the throne stayed taken and the
    heirs could never claim it (Drasah's 2026-10-11 report). Returns
    True when the leave was applied.
    """
    if guild is None or not user_id:
        return False
    wiring = _wiring()
    if wiring.kingdoms_service is None:
        return False
    kingdom_id = ""
    try:
        lord = next(
            (item for item in await wiring.kingdoms_service.lords() if str(item.id) == str(user_id)),
            None,
        )
        kingdom_id = str(getattr(lord, "kingdom_id", "") or "")
    except Exception:
        logger.warning("KINGDOMS: lord lookup before leave failed", exc_info=True)
    try:
        await wiring.kingdoms_service.leave(str(user_id), reason)
    except Exception:
        logger.info("KINGDOMS: leave not applied for %s", user_id, exc_info=True)
        return False
    # ejection-cleanup-v1 (Drasah 30/10): the departed player returns to
    # the NEWCOMER state — his stale member overwrites on the ex-kingdom
    # and his player roles die at the leave seam, not "at some point".
    await _cleanup_departed_player_safe(guild, user_id, kingdom_id)
    if kingdom_id:
        await _refresh_realm_views_for_kingdom_safe(guild, kingdom_id)
    await _refresh_realms_panel_safe(guild, locale)
    await _notify_heirs_safe(guild, locale)
    return True


async def _draw_territories_for_kingdom_safe(kingdom_id: str) -> None:
    """Best-effort per-kingdom draw after an approval (guarded, idempotent).

    Deferred while the season is in ``setup``: the territories are
    distributed for everyone when the admin starts the game.
    """
    wiring = _wiring()
    if wiring.territories_service is None:
        return
    if await _season_phase_safe() == "setup":
        return
    try:
        await wiring.territories_service.draw_initial_for(kingdom_id)
    except Exception:
        logger.warning("KINGDOMS: territory draw for kingdom %s failed", kingdom_id, exc_info=True)


async def _ensure_realms_after_launch(guild: discord.Guild, locale: str) -> None:
    """Provision the approved kingdoms, then purge the orphan categories."""
    wiring = _wiring()
    kingdoms: list[Any] = []
    if wiring.kingdoms_service is not None:
        try:
            kingdoms = await wiring.kingdoms_service.kingdoms()
        except Exception:
            logger.warning("KINGDOM REALMS: kingdom list read failed", exc_info=True)
    try:
        from kingdoms.mods.kingdoms.kingdom_realms import (
            delete_orphan_realm_categories,
            ensure_all_realm_structures,
        )

        await ensure_all_realm_structures(guild, wiring.kingdoms_service)
        await delete_orphan_realm_categories(guild, kingdoms)
    except Exception:
        logger.warning("KINGDOM REALMS: post-launch provisioning failed", exc_info=True)
    # draw the territories BEFORE the salon views read the state — the
    # Territoire panel is built from the live documents (Drasah's
    # 2026-10-11 regression report: the maps never showed up because
    # the views were rendered from the pre-draw store).
    await _draw_territories_after_launch_safe()
    for kingdom in kingdoms:
        # Gaïa deploys its content too (Drasah's 2026-10-11 report: the
        # AI kingdom drew its 8 territories but the Territoire/Patrouille
        # salon views were never pinned — Gaïa has no civs, its slice
        # only carries the territory/patrol state views).
        if str(kingdom.validation) == "approved":
            await _deploy_realm_content_safe(guild, kingdom)
    await _refresh_realms_panel_safe(guild, locale)


class KingdomRealmRenameModal(discord.ui.Modal):
    """The admin form to correct a kingdom's name before validating."""

    new_name: discord.ui.TextInput[KingdomRealmRenameModal] = discord.ui.TextInput(
        label="New name",
        placeholder="Aquitaine",
        max_length=45,
        required=True,
    )

    def __init__(self, locale: str, kingdom_id: str) -> None:
        self.locale = "fr" if str(locale).lower().startswith("fr") else "en"
        self.kingdom_id = kingdom_id
        from kingdoms.mods.kingdoms.kingdom_realms import _strings as realm_strings

        strings = realm_strings(self.locale)
        self.new_name.label = strings["realms_rename_field"][:45]
        super().__init__(title=strings["realms_rename_title"][:45], timeout=300)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from kingdoms.mods.kingdoms.kingdom_realms import (
            _find_realm_category,
            realm_category_name,
        )
        from kingdoms.mods.kingdoms.kingdom_realms import (
            _strings as realm_strings,
        )

        strings = realm_strings(self.locale)
        wiring = _wiring()
        if wiring.kingdoms_service is None:
            await interaction.response.send_message(strings["realms_failed"].format(error="no service"), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            kingdom = await wiring.kingdoms_service.rename_kingdom(self.kingdom_id, (self.new_name.value or "").strip())
        except Exception as exc:
            # realms-midseason-v1: {error} is a NAMED placeholder — a
            # positional .format() raised KeyError 'error' and masked
            # the real cause with silence.
            await interaction.followup.send(strings["realms_failed"].format(error=type(exc).__name__), ephemeral=True)
            return
        if interaction.guild is not None and str(kingdom.validation) == "approved":
            category = _find_realm_category(interaction.guild, kingdom.name)
            if category is not None:
                try:
                    await category.edit(name=realm_category_name(kingdom.name))
                except Exception:
                    logger.warning("KINGDOM REALMS: category rename failed", exc_info=True)
        await interaction.followup.send(strings["realms_done"], ephemeral=True)
        if interaction.guild is not None:
            await _refresh_realms_panel_safe(interaction.guild, self.locale)


class KingdomKingNameButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:king:name-request",
):
    """The restart-proof 🏷️ button a validated King clicks to name their kingdom.

    Sent by DM (fallback: the member's profile channel) when the King
    application is approved — the flow replaces the old free-text DM
    reply (drasah 2026-10-10: a plain reply is silently ignored when
    the state moved on; a button + modal always answers, with the
    reason when something is off).
    """

    def __init__(self, label: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                style=discord.ButtonStyle.primary,
                custom_id="kingdoms:king:name-request",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomKingNameButton:
        """Rebuild the name-request button from the wire."""
        locale = str(interaction.locale) if interaction.locale else "en"
        return cls(_king_name_strings(locale)["king_name_button"][:80])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Check the King state, then open the naming modal."""
        locale = str(interaction.locale) if interaction.locale else "en"
        strings = _king_name_strings(locale)
        wiring = _wiring()
        if wiring.kingdoms_service is None:
            await interaction.response.send_message(
                strings["market_no_service"], ephemeral=True
            )
            return
        player_id = str(getattr(interaction.user, "id", ""))
        lord = await _king_of(wiring.kingdoms_service, player_id)
        if lord is None:
            try:
                lord = next(
                    (
                        item
                        for item in await wiring.kingdoms_service.lords()
                        if str(item.id) == player_id and not item.left
                    ),
                    None,
                )
            except Exception:
                lord = None
        if lord is None or str(lord.role) != "king" or not lord.in_queue or lord.kingdom_id is not None:
            await interaction.response.send_message(
                strings["king_name_state_invalid"], ephemeral=True
            )
            return
        await interaction.response.send_modal(KingdomKingNameModal(locale))


class KingdomKingClaimSelect(
    discord.ui.DynamicItem[discord.ui.Select[Any]],
    template=r"kingdoms:king:claim-select",
):
    """The restart-proof throne select a validated King picks from.

    Drasah's rule (2026-10-11): imposed seasons never offer the naming
    DM — the King claims one of the existing kingdoms instead, and the
    bot answers with the outcome (claimed / already taken / no pending
    application).
    """

    def __init__(self, options: list[discord.SelectOption], placeholder: str) -> None:
        super().__init__(
            discord.ui.Select(
                placeholder=placeholder,
                options=options or [discord.SelectOption(label="—", value="none")],
                custom_id="kingdoms:king:claim-select",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomKingClaimSelect:
        """Rebuild the select from the wire, options fresh from the service."""
        locale = str(interaction.locale) if interaction.locale else "en"
        strings = _king_name_strings(locale)
        options: list[discord.SelectOption] = []
        wiring = _wiring()
        if wiring.kingdoms_service is not None:
            try:
                for kingdom in await wiring.kingdoms_service.available_kingdoms():
                    options.append(
                        discord.SelectOption(label=str(kingdom.name)[:100], value=str(kingdom.id))
                    )
            except Exception:
                logger.info("CANDIDATURES: claim select rebuild failed", exc_info=True)
        return cls(options, strings["king_claim_placeholder"][:100])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Claim the picked throne and answer with the outcome."""
        locale = str(interaction.locale) if interaction.locale else "en"
        strings = _king_name_strings(locale)
        wiring = _wiring()
        player_id = str(getattr(interaction.user, "id", ""))
        kingdom_id = self.item.values[0] if self.item.values else ""
        if wiring.kingdoms_service is None or not kingdom_id or kingdom_id == "none":
            await interaction.response.send_message(
                strings["king_claim_failed"].format("no service"), ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            kingdom = await wiring.kingdoms_service.claim_kingdom(player_id, kingdom_id)
        except Exception as exc:
            logger.info("CANDIDATURES: kingdom claim failed for %s", player_id, exc_info=True)
            await interaction.followup.send(
                strings["king_claim_failed"].format(type(exc).__name__), ephemeral=True
            )
            return
        await interaction.followup.send(
            strings["king_claim_done"].format(kingdom.name), ephemeral=True
        )
        client = getattr(interaction, "client", None)
        for guild in getattr(client, "guilds", []) or []:
            await _after_claim_refresh(guild, locale, kingdom, player_id)


async def _grant_game_roles_at_kingdom_approval_safe(
    guild: discord.Guild | None, wiring: KingdomsPanelWiring, members: list[Any]
) -> None:
    """Grant the game roles to a kingdom's members at its ACCEPTANCE (vision-gating-v1).

    The candidature approval deliberately withholds the Discord role
    until the kingdom is accepted — this seam (the 👑 royaumes panel
    validation) is where a founding King and the lords seated meanwhile
    finally enter the game.
    """
    from kingdoms.mods.kingdoms.kingdom_panels import ROLE_KING, ROLE_LORD
    from kingdoms.mods.kingdoms.kingdom_immersion import clear_outgame_allow_safe

    for lord in members:
        role_key = ROLE_KING if str(getattr(lord, "role", "")) == "king" else ROLE_LORD
        if await _grant_player_mod_role_safe(wiring, guild, str(lord.id), role_key):
            logger.info(
                "CANDIDATURES: game role granted at kingdom approval for %s", lord.id
            )
        try:
            await clear_outgame_allow_safe(guild, str(lord.id))
        except Exception:
            logger.warning("CANDIDATURES: outgame allow clear failed", exc_info=True)


async def _after_claim_refresh(guild: discord.Guild, locale: str, kingdom: Any, player_id: str) -> None:
    """Refresh everything the claim touched (Drasah's stale-view bug).

    The claim changes the realm membership (a new King) — the category
    overwrites must re-sync (the King sees their kingdom), the kingdom's
    state views re-render (le-royaume/seigneurs show the new King), the
    roster/status refresh and the enrollment is announced. Every step
    is best-effort: one failed refresh never blocks the others.
    """
    wiring = _wiring()
    try:
        from kingdoms.mods.kingdoms.kingdom_realms import ensure_all_realm_structures

        await ensure_all_realm_structures(guild, wiring.kingdoms_service)
    except Exception:
        logger.warning("CANDIDATURES: realm overwrites re-sync failed after claim", exc_info=True)
    try:
        from kingdoms.mods.kingdoms.kingdom_realms import ensure_member_realm_access

        if str(player_id).isdigit():
            await ensure_member_realm_access(guild, kingdom, int(player_id))
    except Exception:
        logger.warning("CANDIDATURES: king access guarantee failed after claim", exc_info=True)
    # vision-gating-v1 (Drasah 30/10): the claimed throne is an ACCEPTED
    # kingdom — the claiming King enters the game right here (his
    # candidature approval deliberately withheld the role until then).
    from kingdoms.mods.kingdoms.kingdom_panels import ROLE_KING

    await _grant_player_mod_role_safe(_wiring(), guild, player_id, ROLE_KING)
    try:
        from kingdoms.mods.kingdoms.kingdom_immersion import clear_outgame_allow_safe

        await clear_outgame_allow_safe(guild, player_id)
    except Exception:
        logger.warning("CANDIDATURES: outgame allow clear failed", exc_info=True)
    await _refresh_realm_views_for_kingdom_safe(guild, str(kingdom.id))
    await _refresh_season_status_safe(guild, locale)
    await _refresh_realms_panel_safe(guild, locale)
    await _announce_enrollment_safe(guild, locale, f"<@{player_id}>", str(kingdom.name), True)


class KingdomKingNameModal(discord.ui.Modal):
    """The validated King's form naming their kingdom (flow v2, D70)."""

    name: discord.ui.TextInput[KingdomKingNameModal] = discord.ui.TextInput(
        label="Kingdom name",
        placeholder="Aquitaine",
        max_length=40,
        required=True,
    )

    def __init__(self, locale: str) -> None:
        self.locale = "fr" if str(locale).lower().startswith("fr") else "en"
        strings = _king_name_strings(self.locale)
        self.name.label = strings["king_name_field"][:45]
        super().__init__(title=strings["king_name_modal_title"][:45], timeout=300)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Create the PENDING kingdom and answer with the outcome."""
        strings = _king_name_strings(self.locale)
        wiring = _wiring()
        player_id = str(getattr(interaction.user, "id", ""))
        kingdom_name = (self.name.value or "").strip()
        if wiring.kingdoms_service is None or not kingdom_name:
            await interaction.response.send_message(
                strings["king_name_dm_error"].format("no service"), ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            kingdom = await wiring.kingdoms_service.found_kingdom(player_id, kingdom_name)
        except Exception as exc:
            await interaction.followup.send(
                strings["king_name_dm_error"].format(type(exc).__name__), ephemeral=True
            )
            return
        await interaction.followup.send(
            strings["king_name_dm_received"].format(kingdom.name), ephemeral=True
        )
        client = getattr(interaction, "client", None)
        for guild in getattr(client, "guilds", []) or []:
            await _refresh_realms_panel_safe(guild, self.locale)


class KingdomRealmButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=(
        r"kingdoms:realm:(?P<action>approve|refuse|rename|reking|delete"
        r"|delete-confirm|delete-cancel):(?P<kid>[a-zA-Z0-9_-]+)"
    ),
):
    """The restart-proof validate/refuse/rename/delete buttons of the panel."""

    def __init__(self, action: str, kingdom_id: str, label: str, style: discord.ButtonStyle) -> None:
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                custom_id=f"kingdoms:realm:{action}:{kingdom_id[:90]}",
            )
        )
        self.action = action
        self.kingdom_id = kingdom_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomRealmButton:
        """Rebuild the realm button from the wire."""
        locale = str(interaction.locale) if interaction.locale else "en"
        from kingdoms.mods.kingdoms.kingdom_realms import _strings as realm_strings

        strings = realm_strings(locale)
        action = match.group("action")
        labels = {
            "approve": strings["realms_approve_button"],
            "refuse": strings["realms_refuse_button"],
            "rename": strings["realms_rename_button"],
            "reking": strings["realms_reking_button"],
            "delete": strings["realms_delete_button"],
            "delete-confirm": strings["realms_delete_confirm_button"],
            "delete-cancel": strings["realms_delete_cancel_button"],
        }
        styles = {
            "approve": discord.ButtonStyle.success,
            "refuse": discord.ButtonStyle.danger,
            "rename": discord.ButtonStyle.secondary,
            "reking": discord.ButtonStyle.secondary,
            "delete": discord.ButtonStyle.danger,
            "delete-confirm": discord.ButtonStyle.danger,
            "delete-cancel": discord.ButtonStyle.secondary,
        }
        return cls(action, match.group("kid"), labels[action], styles[action])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Run the kingdom validation chosen on the Royaumes panel."""
        from kingdoms.mods.kingdoms.kingdom_realms import (
            _strings as realm_strings,
        )
        from kingdoms.mods.kingdoms.kingdom_realms import (
            delete_realm_structure,
            ensure_realm_structure,
        )

        locale = str(interaction.locale) if interaction.locale else "en"
        strings = realm_strings(locale)
        wiring = _wiring()
        if not _is_admin(interaction, wiring.bot_admins):
            await interaction.response.send_message("Only admins can validate kingdoms.", ephemeral=True)
            return
        if wiring.kingdoms_service is None or interaction.guild is None:
            await interaction.response.send_message(strings["realms_failed"].format(error="no service"), ephemeral=True)
            return
        if self.action == "rename":
            await interaction.response.send_modal(KingdomRealmRenameModal(locale, self.kingdom_id))
            return
        if self.action == "reking":
            await interaction.response.defer(ephemeral=True)
            from kingdoms.mods.kingdoms.kingdom_panels import ROLE_KING, ROLE_LORD

            # admin-tools-v1 (Drasah 30/10): only the ACTIVE lords of THIS
            # kingdom qualify — the sitting king and the departed never.
            candidates = [
                lord
                for lord in await wiring.kingdoms_service.lords()
                if lord.kingdom_id == self.kingdom_id
                and not lord.left
                and str(lord.role) != "king"
            ]
            if not candidates:
                await interaction.followup.send(strings["realms_reking_empty"], ephemeral=True)
                return
            select = discord.ui.Select(
                placeholder=strings["realms_reking_title"][:100],
                options=[
                    discord.SelectOption(label=lord.display_name[:100], value=str(lord.id))
                    for lord in candidates[:25]
                ],
                min_values=1,
                max_values=1,
            )
            admin_id = interaction.user.id

            async def _crown(pick: discord.Interaction) -> None:
                await pick.response.defer(ephemeral=True)
                lord_id = select.values[0]
                try:
                    kingdom, former, new = await wiring.kingdoms_service.reassign_king(
                        self.kingdom_id, lord_id
                    )
                except Exception as exc:
                    await pick.followup.send(
                        strings["realms_failed"].format(error=type(exc).__name__), ephemeral=True
                    )
                    return
                # Discord-side swap: the former king falls back to Lord,
                # the new one takes the crown (game roles, never Admin).
                if former is not None:
                    await _remove_player_mod_roles_safe(pick.guild, str(former.id))
                    await _grant_player_mod_role_safe(wiring, pick.guild, str(former.id), ROLE_LORD)
                await _remove_player_mod_roles_safe(pick.guild, str(new.id))
                await _grant_player_mod_role_safe(wiring, pick.guild, str(new.id), ROLE_KING)
                await pick.followup.send(
                    strings["realms_reking_done"].format(
                        kingdom=kingdom.name, lord=new.display_name
                    ),
                    ephemeral=True,
                )
                await _log_admin_action_safe(
                    pick.guild,
                    f"👑 <@{admin_id}> a réattribué le trône de **{kingdom.name}** à <@{new.id}>",
                )
                await _refresh_realms_panel_safe(pick.guild, locale)

            select.callback = _crown  # type: ignore[method-assign]
            view = discord.ui.View(timeout=120)
            view.add_item(select)
            await interaction.followup.send(strings["realms_reking_title"], view=view, ephemeral=True)
            return
        if self.action == "delete":
            view = discord.ui.View(timeout=120)
            view.add_item(
                KingdomRealmButton(
                    "delete-confirm",
                    self.kingdom_id,
                    strings["realms_delete_confirm_button"][:80],
                    discord.ButtonStyle.danger,
                )
            )
            view.add_item(
                KingdomRealmButton(
                    "delete-cancel",
                    self.kingdom_id,
                    strings["realms_delete_cancel_button"][:80],
                    discord.ButtonStyle.secondary,
                )
            )
            await interaction.response.send_message(
                f"**{strings['realms_delete_confirm_title']}**\n{strings['realms_delete_confirm_hint']}",
                view=view,
                ephemeral=True,
            )
            return
        if self.action == "delete-cancel":
            await interaction.response.send_message(strings["realms_cancelled"], ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            if self.action == "approve":
                kingdom = await wiring.kingdoms_service.approve_kingdom(self.kingdom_id)
                members = [
                    lord
                    for lord in await wiring.kingdoms_service.lords()
                    if lord.kingdom_id == kingdom.id and not lord.left
                ]
                member_ids = [int(lord.id) for lord in members if str(lord.id).isdigit()]
                await ensure_realm_structure(interaction.guild, kingdom, member_ids)
                # the validation seam used to skip the Alliances message
                # (Drasah's 2026-10-12 "sometimes missing" report) —
                # every provisioning seam writes it, phase-aware.
                from kingdoms.mods.kingdoms.kingdom_realms import (
                    _announce_realm_alliances,
                )

                await _announce_realm_alliances(
                    interaction.guild, kingdom, wiring.kingdoms_service
                )
                await _draw_territories_for_kingdom_safe(kingdom.id)
                await _deploy_realm_content_safe(interaction.guild, kingdom)
                # vision-gating-v1 (Drasah 30/10): the kingdom is now
                # ACCEPTED — its members finally receive their game roles.
                await _grant_game_roles_at_kingdom_approval_safe(
                    interaction.guild, wiring, members
                )
                await _log_admin_action_safe(
                    interaction.guild,
                    f"✅ <@{interaction.user.id}> a validé le royaume **{kingdom.name}**",
                )
            elif self.action == "delete-confirm":
                kingdom = await wiring.kingdoms_service.delete_kingdom(self.kingdom_id)
                await delete_realm_structure(interaction.guild, kingdom.name)
                await _log_admin_action_safe(
                    interaction.guild,
                    f"🗑️ <@{interaction.user.id}> a supprimé le royaume **{kingdom.name}**",
                )
            else:
                kingdom = await wiring.kingdoms_service.refuse_kingdom(self.kingdom_id)
                await delete_realm_structure(interaction.guild, kingdom.name)
                await _log_admin_action_safe(
                    interaction.guild,
                    f"❌ <@{interaction.user.id}> a refusé le royaume **{kingdom.name}**",
                )
        except Exception as exc:
            # realms-midseason-v1 (Drasah 29/10): {error} is a NAMED
            # placeholder — the positional .format() raised KeyError
            # 'error', so Drasah saw NO ephemeral message at all and
            # the panel refresh below was skipped: the Valider button
            # stayed open on a kingdom already flipped APPROVED in DB.
            await interaction.followup.send(
                strings["realms_failed"].format(error=type(exc).__name__), ephemeral=True
            )
            # the kingdom was approved (or refused/deleted) BEFORE the
            # crash — the panel must reflect the DB state even on a
            # partial provisioning failure, never leave a stale button.
            await _refresh_realms_panel_safe(interaction.guild, locale)
            return
        if self.action == "delete-confirm":
            await interaction.followup.send(strings["realms_deleted"], ephemeral=True)
        else:
            await interaction.followup.send(strings["realms_done"], ephemeral=True)
        await _refresh_realms_panel_safe(interaction.guild, locale)
