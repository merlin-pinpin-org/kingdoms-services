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
    "KingdomKingClaimSelect",
    "KingdomKingNameButton",
    "KingdomKingNameModal",
    "KingdomMarketActionButton",
    "KingdomMarketTechButton",
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
    channel_service: Any = None
    registry: Any = None
    territories_service: Any = None
    attacks_service: Any = None
    economy_service: Any = None
    diplomacy_service: Any = None


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
            channel_service=getattr(bot, "channel_service", None),
            registry=getattr(bot, "registry", None),
            territories_service=getattr(bot, "kingdoms_territories_service", None),
            attacks_service=getattr(bot, "kingdoms_attacks_service", None),
            economy_service=getattr(bot, "kingdoms_economy_service", None),
            diplomacy_service=getattr(bot, "kingdoms_diplomacy_service", None),
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
        KingdomMarketTechButton,
        KingdomMarketActionButton,
        KingdomRealmButton,
        KingdomKingNameButton,
        KingdomKingClaimSelect,
    )


def _is_admin(interaction: discord.Interaction, bot_admins: tuple[str, ...]) -> bool:
    user_id = getattr(interaction.user, "id", None)
    if user_id is not None and str(user_id) in bot_admins:
        return True
    permissions = getattr(interaction.user, "guild_permissions", None)
    return bool(permissions and permissions.administrator)


def _strings(locale: str) -> dict[str, Any]:
    from kingdoms.mods.kingdoms.kingdom_panels import _strings as panels_strings

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
        """Apply the decision through the live wiring (roles, enrollment, notes)."""
        from kingdoms.mods.kingdoms.kingdom_panels import KINGDOM_MOD, ROLE_KING, ROLE_LORD

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
            role_key = ROLE_KING if is_king else ROLE_LORD
            kingdom_name = _candidature_kingdom_name(content, strings)
            enrolled = False
            if applicant is not None and wiring.kingdoms_service is not None and interaction.guild is not None:
                enrollment_note, enrolled = await _enroll_applicant(
                    wiring.kingdoms_service,
                    applicant.group(1),
                    is_king=is_king,
                    kingdom_name=kingdom_name,
                    strings=strings,
                )
                note += " " + enrollment_note
            if wiring.mod_roles_service is not None and applicant is not None and interaction.guild is not None:
                try:
                    await wiring.mod_roles_service.assign_mod_role(
                        str(interaction.guild.id), applicant.group(1), KINGDOM_MOD, role_key
                    )
                    note += " " + strings["role_assigned"].format(role_key)
                except Exception:
                    logger.warning("CANDIDATURES: role assignment failed", exc_info=True)
                    note += " " + strings["no_service"]
            if interaction.guild is not None and applicant is not None:
                await _send_welcome(interaction.guild, applicant.group(1), strings)
                if is_king and kingdom_name is None:
                    await _send_king_kingdom_request(interaction.guild, applicant.group(1), locale)
            if interaction.guild is not None:
                await _refresh_season_status_safe(interaction.guild, locale)
                if enrolled and applicant is not None:
                    await _announce_enrollment_safe(
                        interaction.guild,
                        locale,
                        f"<@{applicant.group(1)}>",
                        kingdom_name,
                        is_king,
                    )
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
        MarriageError,
        MarriageExclusivityError,
    )
    from kingdoms.mods.kingdoms.economy import (
        EconomyLimitReachedError,
        GuardAlreadyActiveError,
        InsufficientPointsError,
        TerritoryProtectedError,
    )

    if isinstance(exc, (InsufficientPointsError, InsufficientTechPointsError)):
        return str(strings["market_err_insufficient"])
    if isinstance(exc, (EconomyLimitReachedError, TechnologyLimitReachedError)):
        return str(strings["market_err_limit"])
    if isinstance(exc, TerritoryProtectedError):
        return str(strings["market_err_protected"])
    if isinstance(exc, GuardAlreadyActiveError):
        return str(strings["market_err_guard_active"])
    if isinstance(exc, AlreadyMarriedError):
        return str(strings["market_err_already_married"])
    if isinstance(exc, MarriageExclusivityError):
        return str(strings["market_err_civ_taken"])
    if isinstance(exc, MarriageError):
        return str(strings["market_err_civ_unknown"])
    logger.warning("KINGDOMS MARKET: purchase failed", exc_info=True)
    return str(strings["market_err_unknown"]).format(type(exc).__name__)


async def _market_action_select_view(
    wiring: KingdomsPanelWiring,
    king: Any,
    action: str,
    strings: dict[str, Any],
) -> discord.ui.View | None:
    """Build the ephemeral territory select of one special action (None when empty)."""
    if wiring.territories_service is None:
        return None
    kingdom_id = king.kingdom_id or ""
    territories = await wiring.territories_service.territories()
    owned = [t for t in territories if t.owner_kingdom_id == kingdom_id]
    candidates = owned if action == "garde_royale" else [
        t for t in territories if t.owner_kingdom_id != kingdom_id
    ]
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
        await target.response.defer(ephemeral=True)
        economy = wiring.economy_service
        if economy is None:
            await target.followup.send(strings["market_no_service"], ephemeral=True)
            return
        try:
            if action == "corruption":
                territory = await economy.buy_corruption(kingdom_id, chosen[0])
            else:
                territory = await economy.buy_royal_guard(kingdom_id, chosen[0])
        except Exception as exc:
            await target.followup.send(_market_error_note(exc, strings), ephemeral=True)
            return
        detail = strings["market_territory_line"].format(
            territory.map_key, territory.owner_kingdom_id
        )
        await target.followup.send(
            strings["market_buy_ok_territory"].format(strings["market_action"][action], detail),
            ephemeral=True,
        )
        await _refresh_realm_views_for_kingdom_safe(target.guild, kingdom_id)

    select.callback = on_choose  # type: ignore[method-assign, assignment]
    view = discord.ui.View(timeout=600)
    view.add_item(select)
    return view


def _market_patrouille_view(strings: dict[str, Any]) -> discord.ui.View:
    """Build the ephemeral patrol-slot select (D68: daily 2h tranches)."""
    select: discord.ui.Select[Any] = discord.ui.Select(
        custom_id="kingdoms:market:choose:patrouille",
        placeholder=strings["market_action"]["patrouille"][:100],
        options=[
            discord.SelectOption(
                label=strings["market_slot_line"].format(start, start + 2),
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
        await target.response.defer(ephemeral=True)
        wiring = _wiring()
        economy = wiring.economy_service
        if economy is None:
            await target.followup.send(strings["market_no_service"], ephemeral=True)
            return
        king = await _king_of(wiring.kingdoms_service, str(target.user.id))
        if king is None:
            await target.followup.send(strings["market_not_king"], ephemeral=True)
            return
        slot = int(chosen[0])
        try:
            await economy.buy_patrouille(king.kingdom_id or "", slot)
        except Exception as exc:
            await target.followup.send(_market_error_note(exc, strings), ephemeral=True)
            return
        try:
            wallet = await economy.wallet(king.kingdom_id or "")
        except Exception:
            wallet = 0
        await target.followup.send(
            strings["market_patrouille_ok"].format(slot, slot + 2, wallet),
            ephemeral=True,
        )
        await _refresh_realm_views_for_kingdom_safe(target.guild, king.kingdom_id or "")

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

    async def on_choose(target: discord.Interaction[Any]) -> None:
        values = getattr(target, "data", None) or {}
        chosen = [str(v) for v in values.get("values", [])]
        if not chosen:
            return
        await target.response.defer(ephemeral=True)

        async def spend(_lord_id: str, cost: int) -> None:
            await economy.spend_points(kingdom_id, cost)

        try:
            await diplomacy.arranged_marriage(lord_id, chosen[0], spend_points=spend)
        except Exception as exc:
            await target.followup.send(_market_error_note(exc, strings), ephemeral=True)
            return
        try:
            wallet = await economy.wallet(kingdom_id)
        except Exception:
            wallet = 0
        await target.followup.send(
            strings["market_mariage_ok"].format(chosen[0], wallet),
            ephemeral=True,
        )
        await _refresh_realm_views_for_kingdom_safe(target.guild, kingdom_id)

    select.callback = on_choose  # type: ignore[method-assign, assignment]
    view = discord.ui.View(timeout=600)
    view.add_item(select)
    return view


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
        label = f"{strings['market_tech'][tech]}"
        return cls(tech, label, discord.ButtonStyle.primary)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Debit the kingdom treasury and buy the technology (D9/D36)."""
        locale = str(interaction.locale) if interaction.locale else "en"
        strings = _strings(locale)
        label = strings["market_tech"][self.tech]
        wiring = _wiring()
        await interaction.response.defer(ephemeral=True)
        if wiring.economy_service is None:
            await interaction.followup.send(strings["market_no_service"], ephemeral=True)
            return
        king = await _king_of(wiring.kingdoms_service, str(interaction.user.id))
        if king is None:
            await interaction.followup.send(strings["market_not_king"], ephemeral=True)
            return
        try:
            await wiring.economy_service.buy_combat_technology(king.kingdom_id or "", self.tech)
        except Exception as exc:
            await interaction.followup.send(_market_error_note(exc, strings), ephemeral=True)
            return
        try:
            wallet = await wiring.economy_service.wallet(king.kingdom_id or "")
        except Exception:
            wallet = 0
        await interaction.followup.send(
            strings["market_buy_ok"].format(label, wallet), ephemeral=True
        )
        await _refresh_realm_views_for_kingdom_safe(interaction.guild, king.kingdom_id or "")


class KingdomMarketActionButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=(
        r"kingdoms:market:action:(?P<action>explorateur|corruption|garde_royale"
        r"|patrouille|mariage_arrange)"
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
        return cls(action, strings["market_action"][action], discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Buy immediately (Explorateur) or open the territory select."""
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
        if self.action == "explorateur":
            # D48: the Explorateur draws a random undrawn map, no menu.
            await interaction.response.defer(ephemeral=True)
            try:
                territory = await wiring.economy_service.buy_explorateur(king.kingdom_id or "")
            except Exception as exc:
                await interaction.followup.send(
                    _market_error_note(exc, strings), ephemeral=True
                )
                return
            detail = strings["market_territory_line"].format(
                territory.map_key, territory.owner_kingdom_id
            )
            await interaction.followup.send(
                strings["market_buy_ok_territory"].format(
                    strings["market_action"]["explorateur"], detail
                ),
                ephemeral=True,
            )
            await _refresh_realm_views_for_kingdom_safe(interaction.guild, king.kingdom_id or "")
            return
        if self.action == "patrouille":
            # D68: the King picks the daily 2h no-aggression slot.
            await interaction.response.send_message(
                strings["market_choose_slot"],
                view=_market_patrouille_view(strings),
                ephemeral=True,
            )
            return
        if self.action == "mariage_arrange":
            # D60/D74: the King picks the lord, then the civilization.
            marry_view = await _market_marry_lord_view(wiring, king, strings)
            if marry_view is None:
                await interaction.response.send_message(
                    strings["market_no_options"], ephemeral=True
                )
                return
            await interaction.response.send_message(
                strings["market_choose_lord"], view=marry_view, ephemeral=True
            )
            return
        view = await _market_action_select_view(wiring, king, self.action, strings)
        if view is None:
            await interaction.response.send_message(strings["market_no_options"], ephemeral=True)
            return
        prompt = {
            "corruption": strings["market_choose_corrupt"],
            "garde_royale": strings["market_choose_guard"],
        }[self.action]
        await interaction.response.send_message(prompt, view=view, ephemeral=True)


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
        try:
            await wiring.kingdoms_service.launch()
        except Exception as exc:
            await interaction.followup.send(strings["launch_failed"].format(type(exc).__name__), ephemeral=True)
            return
        await interaction.followup.send(strings["launch_done_free"], ephemeral=True)
        if interaction.guild is not None:
            await _refresh_season_status_safe(interaction.guild, self.locale)
            await _ensure_realms_after_launch(interaction.guild, self.locale)


class KingdomAdminButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=(
        r"kingdoms:admin:(?P<action>remove|reset|reset-confirm|reset-cancel"
        r"|deploy|deploy-confirm|deploy-cancel|sync|sync-confirm|sync-cancel"
        r"|status|assign|add-kingdom|launch|start-season|season-mode"
        r"|back-setup|back-setup-confirm|back-setup-cancel"
        r"|end-season|end-season-confirm|end-season-cancel)"
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
            "season-mode": strings["season_mode_button"],
            "back-setup": strings["back_setup_button"],
            "back-setup-confirm": strings["back_setup_confirm_button"],
            "back-setup-cancel": strings["back_setup_cancel_button"],
            "end-season": strings["end_season_button"],
            "end-season-confirm": strings["end_season_confirm_button"],
            "end-season-cancel": strings["end_season_cancel_button"],
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
            "season-mode": discord.ButtonStyle.secondary,
            "back-setup": discord.ButtonStyle.danger,
            "back-setup-confirm": discord.ButtonStyle.success,
            "back-setup-cancel": discord.ButtonStyle.secondary,
            "end-season": discord.ButtonStyle.danger,
            "end-season-confirm": discord.ButtonStyle.success,
            "end-season-cancel": discord.ButtonStyle.secondary,
        }
        return cls(action, labels[action][:80], styles[action])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Run the admin action chosen on the Paramètres panel."""

        async def open_remove(_: discord.Interaction, locale: str) -> None:
            await interaction.response.send_modal(KingdomRemovePlayerModal(locale))

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
) -> tuple[str, bool]:
    """Enroll the approved applicant; answer with a note and a success flag."""
    from kingdoms.mods.kingdoms.service import KING_ROLE, LORD_ROLE

    display_name = f"<@{player_id}>"
    if is_king and not kingdom_name:
        try:
            await kingdoms_service.enroll_king_awaiting_kingdom(player_id, display_name)
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
                player_id, display_name, KING_ROLE, proposed_name=kingdom_name
            )
        else:
            lord = await kingdoms_service.enroll(
                player_id, display_name, LORD_ROLE, kingdom_name=kingdom_name
            )
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
    except Exception:
        logger.info("KINGDOMS: season status refresh skipped", exc_info=True)


async def _announce_enrollment_safe(
    guild: discord.Guild,
    locale: str,
    display_name: str,
    kingdom_name: str | None,
    is_king: bool,
) -> None:
    """Best-effort Géopolitique announcement + Seigneurs roster refresh."""
    try:
        from kingdoms.mods.kingdoms.kingdom_content import announce_enrollment, refresh_lords_roster

        wiring = _wiring()
        if display_name:
            await announce_enrollment(guild, locale, display_name, kingdom_name, is_king)
        await refresh_lords_roster(guild, locale, wiring.kingdoms_service)
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


async def _send_king_name_request(guild: discord.Guild, applicant_id: str, locale: str) -> None:
    """DM the approved King asking for their kingdom's name (flow v2, D70)."""
    member = guild.get_member(int(applicant_id))
    if member is None:
        try:
            member = await guild.fetch_member(int(applicant_id))
        except Exception:
            member = None
    if member is None:
        return
    strings = _king_name_strings(locale)
    request = f"# {strings['king_name_dm_title']}\n{strings['king_name_dm_body']}"
    view = discord.ui.View(timeout=None)
    view.add_item(KingdomKingNameButton(strings["king_name_button"][:80]))
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
    if season is not None and season.imposed_kingdoms:
        try:
            available = await wiring.kingdoms_service.available_kingdoms()
        except Exception:
            logger.info("CANDIDATURES: available kingdoms lookup failed", exc_info=True)
            available = []
        if available:
            await _send_king_claim_request(guild, applicant_id, locale, available)
        else:
            await _send_king_awaiting_kingdom(guild, applicant_id, locale)
        return
    await _send_king_name_request(guild, applicant_id, locale)


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
        await wiring.kingdoms_service.reset()
        await wiring.kingdoms_service.launch()
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
        in_realm = (
            category is not None
            and _slug(getattr(category, "name", "")).startswith("royaume-")
        )
        # realm salons die with their kingdom; orphans at the guild root
        # (leftovers of an older reset) match a realm salon slug
        realm_leftover = (
            category is None and _slug(channel.name) in realm_slugs
        )
        name = _slug(channel.name)
        if (
            in_epoch
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
        if slug in structure_names or slug.startswith("royaume-"):
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
        if not kingdom.is_gaia and str(kingdom.validation) == "approved":
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
    if kingdom is not None and not kingdom.is_gaia:
        await _deploy_realm_content_safe(guild, kingdom)


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
    for kingdom in kingdoms:
        if not kingdom.is_gaia and str(kingdom.validation) == "approved":
            await _deploy_realm_content_safe(guild, kingdom)
    await _draw_territories_after_launch_safe()
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
            await interaction.response.send_message(strings["realms_failed"].format("no service"), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            kingdom = await wiring.kingdoms_service.rename_kingdom(self.kingdom_id, (self.new_name.value or "").strip())
        except Exception as exc:
            await interaction.followup.send(strings["realms_failed"].format(type(exc).__name__), ephemeral=True)
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
        r"kingdoms:realm:(?P<action>approve|refuse|rename|delete"
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
            "delete": strings["realms_delete_button"],
            "delete-confirm": strings["realms_delete_confirm_button"],
            "delete-cancel": strings["realms_delete_cancel_button"],
        }
        styles = {
            "approve": discord.ButtonStyle.success,
            "refuse": discord.ButtonStyle.danger,
            "rename": discord.ButtonStyle.secondary,
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
            await interaction.response.send_message(strings["realms_failed"].format("no service"), ephemeral=True)
            return
        if self.action == "rename":
            await interaction.response.send_modal(KingdomRealmRenameModal(locale, self.kingdom_id))
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
                await _draw_territories_for_kingdom_safe(kingdom.id)
                await _deploy_realm_content_safe(interaction.guild, kingdom)
            elif self.action == "delete-confirm":
                kingdom = await wiring.kingdoms_service.delete_kingdom(self.kingdom_id)
                await delete_realm_structure(interaction.guild, kingdom.name)
            else:
                kingdom = await wiring.kingdoms_service.refuse_kingdom(self.kingdom_id)
                await delete_realm_structure(interaction.guild, kingdom.name)
        except Exception as exc:
            await interaction.followup.send(strings["realms_failed"].format(type(exc).__name__), ephemeral=True)
            return
        if self.action == "delete-confirm":
            await interaction.followup.send(strings["realms_deleted"], ephemeral=True)
        else:
            await interaction.followup.send(strings["realms_done"], ephemeral=True)
        await _refresh_realms_panel_safe(interaction.guild, locale)


