"""Kingdoms Chef Mercenaire panel — the taverne auction surface (T2b).

``MercenaryService`` (mercenary.py) owns the evening auction; this
file is its Discord surface: ONE pinned message in the ``🍺 taverne``
salon carrying one restart-proof bid button per lot plus a withdraw
button. The panel reads ONLY this module's FR/EN strings dict (the
per-module strings rule of the Kingdoms surfaces).

chef-mercenaire-v1 (Drasah 24/10): between 20h00 and 21h00 REAL Paris
time the Chief offers five civilizations with a VISIBLE number of
draft uses; the bids open at 20h15, the base close sits at 20h30, an
anti-snipe bid pushes the close +1 minute capped at the 20h45 HARD
close, and the results stay displayed until the Chief leaves. A click
opens the bid modal (écus); the escrow, the ONE-lot-at-a-time move and
the withdrawal refund live in the service.

The panel is deployed by the channel pass (``_deploy_channel_panel``,
slug ``taverne``) and refreshed in place through a marker — a redeploy
never duplicates the message.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import discord

from kingdoms.mods.kingdoms.panel_messages import NO_MENTIONS, channel_messages, message_text

logger = logging.getLogger("kingdoms.mercenary_panel")

MERCENARY_PANEL_MARKER = "<!-- kingdoms-mercenary -->"
DEFAULT_TZ = "Europe/Paris"

_TZ = ZoneInfo(DEFAULT_TZ)

_STRINGS: dict[str, dict[str, str]] = {
    "fr": {
        "panel_title": "⚔️ Chef Mercenaire",
        "panel_absent": (
            "Le Chef Mercenaire passe chaque soir de **20h00 à 21h00** "
            "(heure de Paris) : cinq civilisations tirées au sort, "
            "chacune avec un nombre d'**usages de draft visible avant "
            "l'enchère**. Mise de départ : 1 écu."
        ),
        "panel_rules": (
            "• Une seule civilisation mercenaire par seigneur à la fois.\n"
            "• Une enchère en **écus** place le lot, une seule enchère à "
            "la fois (une nouvelle offre déplace la précédente — les "
            "écus sont réinjectés).\n"
            "• Le retrait rembourse ; si le plus offrant se retire, le "
            "second remporte le lot.\n"
            "• 1 usage = 1 draft ; la civilisation s'évanouit à "
            "l'épuisement des usages."
        ),
        "panel_live_header": "**Les lots du soir**",
        "panel_results_header": "**Résultats du soir**",
        "panel_bids_soon": "Les enchères ouvrent à **{time}** · clôture {close}.",
        "panel_bids_open": "Clôture à **{time}** (anti-snipe +1 min, fermeture dure {hard}).",
        "lot_line_open_bid": "• **{civ}** — {uses} usage(s) · meilleure offre : **{amount} écu(s)**",
        "lot_line_open_none": "• **{civ}** — {uses} usage(s) · aucune offre",
        "lot_line_closed_won": "• **{civ}** ({uses} usage(s)) — remportée par {winner}",
        "lot_line_closed_none": "• **{civ}** ({uses} usage(s)) — invendue",
        "bid_button": "Enchérir · {civ}",
        "withdraw_button": "Retirer mon enchère",
        "modal_title": "Enchère — {civ}",
        "modal_label": "Ton offre en écus",
        "modal_placeholder": "ex : 2",
        "bid_done": (
            "⚔️ Enchère de **{amount} écu(s)** placée sur **{civ}** — "
            "bourse restante : **{purse} écu(s)**."
        ),
        "withdraw_done": "Enchère retirée — les écus réinjectés (bourse : **{purse} écus**).",
        "withdraw_none": "Tu n'as aucune enchère en cours sur le marché.",
        "no_service": "Le Chef Mercenaire n'est pas branché sur cette instance.",
        "not_enrolled": "Tu n'es pas un seigneur actif de la saison en cours.",
        "season_closed": "La saison est terminée — le Chef est parti.",
        "window": "Le Chef n'est pas là — il passe de **20h00 à 21h00** (heure de Paris).",
        "no_market": "Aucun marché en cours — le Chef arrive à 20h00 (heure de Paris).",
        "not_open_yet": "⏳ Les enchères ouvrent à **{time}**.",
        "closed": "Le marché est fermé — le Chef revient à 20h00 (heure de Paris).",
        "lot_not_found": "Ce lot n'est plus sur la table du Chef.",
        "bid_too_low": (
            "⚖️ L'offre doit dépasser **strictement** la meilleure enchère "
            "(actuelle : **{current} écu(s)**, mise de départ {starting})."
        ),
        "insufficient": "🪙 Il te faut **{price} écus** — ta bourse n'en compte que {purse}.",
        "already_held": "Tu détiens déjà une civilisation mercenaire — une seule à la fois.",
        "failed": "L'enchère a échoué : `{message}`.",
    },
    "en": {
        "panel_title": "⚔️ Mercenary Chief",
        "panel_absent": (
            "The Mercenary Chief stands every evening from **8:00 PM to "
            "9:00 PM** (Paris time): five civilizations drawn at random, "
            "each with a **draft-uses count visible before the bid**. "
            "Starting bid: 1 écu."
        ),
        "panel_rules": (
            "• ONE mercenary civilization per lord at a time.\n"
            "• A bid in **écus** holds the lot, one lot at a time (a new "
            "offer MOVES the previous one — the écus are re-injected).\n"
            "• The withdrawal refunds; when the highest bidder withdraws "
            "the SECOND highest takes the lot.\n"
            "• 1 use = 1 draft; the civilization vanishes when the uses "
            "run out."
        ),
        "panel_live_header": "**Tonight's lots**",
        "panel_results_header": "**Tonight's results**",
        "panel_bids_soon": "The bids open at **{time}** · close {close}.",
        "panel_bids_open": "Close at **{time}** (anti-snipe +1 min, hard close {hard}).",
        "lot_line_open_bid": "• **{civ}** — {uses} use(s) · highest bid: **{amount} écu(s)**",
        "lot_line_open_none": "• **{civ}** — {uses} use(s) · no bid",
        "lot_line_closed_won": "• **{civ}** ({uses} use(s)) — won by {winner}",
        "lot_line_closed_none": "• **{civ}** ({uses} use(s)) — unsold",
        "bid_button": "Bid · {civ}",
        "withdraw_button": "Withdraw my bid",
        "modal_title": "Bid — {civ}",
        "modal_label": "Your bid in écus",
        "modal_placeholder": "e.g. 2",
        "bid_done": (
            "⚔️ Bid of **{amount} écu(s)** placed on **{civ}** — purse "
            "left: **{purse} écu(s)**."
        ),
        "withdraw_done": "Bid withdrawn — the écus re-injected (purse: **{purse} écus**).",
        "withdraw_none": "You hold no bid on the market.",
        "no_service": "The Mercenary Chief service is not wired on this instance.",
        "not_enrolled": "You are not an active lord of the current season.",
        "season_closed": "The season is over — the Chief is gone.",
        "window": "The Chief is not here — he stands **8:00-9:00 PM** (Paris time).",
        "no_market": "No live market — the Chief arrives at 8:00 PM (Paris time).",
        "not_open_yet": "⏳ The bids open at **{time}**.",
        "closed": "The market is closed — the Chief returns at 8:00 PM (Paris time).",
        "lot_not_found": "This lot is no longer on the Chief's table.",
        "bid_too_low": (
            "⚖️ A bid must **strictly exceed** the current highest "
            "(now: **{current} écu(s)**, starting bid {starting})."
        ),
        "insufficient": "🪙 You need **{price} écus** — your purse only holds {purse}.",
        "already_held": "You already hold a mercenary civilization — one at a time.",
        "failed": "The bid failed: `{message}`.",
    },
}


def _strings(locale: str) -> dict[str, str]:
    """Return the module's strings of a locale (FR default)."""
    return _STRINGS["fr"] if str(locale).lower().startswith("fr") else _STRINGS["en"]


def _fmt_paris(timestamp: int) -> str:
    """Format a unix timestamp on the players' wall clock (Paris)."""
    moment = datetime.fromtimestamp(timestamp, tz=_TZ)
    return f"{moment:%Hh%M}"


def _error_context(exc: Exception) -> dict[str, str]:
    """Return the serializable context of a mod error (empty fallback)."""
    context = getattr(exc, "context", None)
    if isinstance(context, dict):
        return {str(key): str(value) for key, value in context.items()}
    entries = getattr(context, "entries", None)
    if isinstance(entries, dict):
        return {str(key): str(value) for key, value in entries.items()}
    return {}


def _error_note(exc: Exception, strings: dict[str, str]) -> str:
    """Map a service error to its player-facing note (module strings)."""
    from kingdoms.mods.kingdoms.attacks import NotEnrolledError
    from kingdoms.mods.kingdoms.mercenary import (
        MercenaryAlreadyHeldError,
        MercenaryBidTooLowError,
        MercenaryInsufficientPurseError,
        MercenaryLotNotFoundError,
        MercenaryMarketClosedError,
        MercenaryNoMarketError,
        MercenaryNotOpenYetError,
        MercenarySeasonClosedError,
        MercenaryWindowError,
    )

    if isinstance(exc, MercenaryNotOpenYetError):
        return strings["not_open_yet"]
    if isinstance(exc, MercenaryMarketClosedError):
        return strings["closed"]
    if isinstance(exc, MercenaryWindowError):
        return strings["window"]
    if isinstance(exc, MercenaryNoMarketError):
        return strings["no_market"]
    if isinstance(exc, MercenarySeasonClosedError):
        return strings["season_closed"]
    if isinstance(exc, MercenaryLotNotFoundError):
        return strings["lot_not_found"]
    if isinstance(exc, MercenaryBidTooLowError):
        context = _error_context(exc)
        return strings["bid_too_low"].format(
            current=context.get("current", "?"),
            starting=context.get("starting", "?"),
        )
    if isinstance(exc, MercenaryInsufficientPurseError):
        context = _error_context(exc)
        return strings["insufficient"].format(
            price=context.get("price", "?"),
            purse=context.get("purse", "?"),
        )
    if isinstance(exc, MercenaryAlreadyHeldError):
        return strings["already_held"]
    if isinstance(exc, NotEnrolledError):
        return strings["not_enrolled"]
    return strings["failed"].format(message=getattr(exc, "message", None) or str(exc))


def _civ_names(wiring: Any) -> dict[str, str]:
    """Return the key → display-name map of the civilization catalogue."""
    service = getattr(wiring, "kingdoms_service", None)
    catalog = getattr(getattr(service, "config", None), "civilizations", ()) or ()
    return {
        str(getattr(civ, "key", civ)): str(getattr(civ, "display_name", civ)) for civ in catalog
    }


def _civ_label(civ_key: str, civ_names: dict[str, str]) -> str:
    """Return a civilization's display name (its key when unknown)."""
    return civ_names.get(str(civ_key), str(civ_key))


def build_mercenary_content(doc: dict[str, Any] | None, locale: str) -> str:
    """Build the pinned Chief message content (marker + live market)."""
    strings = _strings(locale)
    lines: list[str] = [MERCENARY_PANEL_MARKER, f"**{strings['panel_title']}**"]
    if not doc:
        lines += [strings["panel_absent"], "", strings["panel_rules"]]
        return "\n".join(lines)
    if not doc:
        lines += [strings["panel_absent"], "", strings["panel_rules"]]
        return "\n".join(lines)
    if doc.get("closed"):
        # the results stay displayed until the Chief leaves (21h) —
        # the next day's open replaces the message in place.
        lines.append(f"**{strings['panel_results_header']}**")
        for lot in doc.get("lots", []):
            civ = str(lot.get("civ"))
            uses = int(lot.get("uses", 0) or 0)
            winner = lot.get("winner")
            if winner:
                lines.append(
                    strings["lot_line_closed_won"].format(
                        civ=civ, uses=uses, winner=f"<@{winner}>"
                    )
                )
            else:
                lines.append(strings["lot_line_closed_none"].format(civ=civ, uses=uses))
        return "\n".join(lines)
    now_ts = int(datetime.now(UTC).timestamp())
    bids_open_at = int(doc.get("bids_open_at", 0) or 0)
    if now_ts < bids_open_at:
        lines.append(
            strings["panel_bids_soon"].format(
                time=_fmt_paris(bids_open_at),
                close=_fmt_paris(int(doc.get("closes_at", 0) or 0)),
            )
        )
    else:
        lines.append(
            strings["panel_bids_open"].format(
                time=_fmt_paris(int(doc.get("closes_at", 0) or 0)),
                hard=_fmt_paris(int(doc.get("hard_close_at", 0) or 0)),
            )
        )
    lines += ["", f"**{strings['panel_live_header']}**"]
    for lot in doc.get("lots", []):
        civ = str(lot.get("civ"))
        uses = int(lot.get("uses", 0) or 0)
        bids = lot.get("bids", []) or []
        highest = max((int(bid.get("amount", 0) or 0) for bid in bids), default=0)
        if highest > 0:
            lines.append(
                strings["lot_line_open_bid"].format(civ=civ, uses=uses, amount=highest)
            )
        else:
            lines.append(strings["lot_line_open_none"].format(civ=civ, uses=uses))
    lines += ["", strings["panel_rules"]]
    return "\n".join(lines)


def build_mercenary_view(doc: dict[str, Any] | None, civ_names: dict[str, str]) -> discord.ui.View:
    """Build the Chief's view — one bid button per LIVE lot + withdrawal."""
    view = discord.ui.View(timeout=None)
    if not doc or doc.get("closed"):
        return view
    for lot in doc.get("lots", []):
        civ = str(lot.get("civ"))
        view.add_item(KingdomMercenaryBidButton(civ, _civ_label(civ, civ_names)))
    view.add_item(KingdomMercenaryWithdrawButton())
    return view


class KingdomMercenaryBidButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:mercenary:bid:(?P<civ>[a-z0-9_]+)",
):
    """The restart-proof bid button of one market lot."""

    def __init__(self, civ: str, civ_label: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=f"⚔️ {civ_label}"[:80],
                custom_id=f"kingdoms:mercenary:bid:{civ}",
                style=discord.ButtonStyle.primary,
            )
        )
        self.civ = civ

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomMercenaryBidButton:
        """Rebuild the bid button from the wire."""
        from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

        civ = match.group("civ")
        wiring = _wiring()
        doc = await _read_market_safe(wiring)
        label = civ
        if doc and not doc.get("closed"):
            lot = next(
                (item for item in doc.get("lots", []) if str(item.get("civ")) == civ), None
            )
            if lot is not None:
                label = _civ_label(civ, _civ_names(wiring))
        return cls(civ, label)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the ephemeral bid modal of this lot."""
        locale = str(interaction.locale) if interaction.locale else "fr"
        strings = _strings(locale)
        await interaction.response.send_modal(
            KingdomMercenaryBidModal(self.civ, strings)
        )


class KingdomMercenaryBidModal(discord.ui.Modal):
    """The ephemeral écus bid sheet of one lot."""

    amount: discord.ui.TextInput[KingdomMercenaryBidModal] = discord.ui.TextInput(
        label="Écus",
        placeholder="2",
        min_length=1,
        max_length=4,
        required=True,
    )

    def __init__(self, civ: str, strings: dict[str, str]) -> None:
        super().__init__(title=strings["modal_title"].format(civ=civ)[:45], timeout=300)
        self.civ = civ
        self.strings = strings
        self.amount.label = strings["modal_label"][:45]
        self.amount.placeholder = strings["modal_placeholder"][:100]

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Escrow the bid and answer with the live purse."""
        from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

        wiring = _wiring()
        service = getattr(wiring, "mercenary_service", None)
        if service is None:
            await interaction.response.send_message(self.strings["no_service"], ephemeral=True)
            return
        try:
            value = int(str(self.amount.value).strip())
        except ValueError:
            await interaction.response.send_message(
                self.strings["failed"].format(message="nombre entier d'écus attendu"),
                ephemeral=True,
            )
            return
        try:
            doc, lord = await service.place_bid(str(interaction.user.id), self.civ, value)
        except Exception as exc:
            await interaction.response.send_message(_error_note(exc, self.strings), ephemeral=True)
            return
        await interaction.response.send_message(
            self.strings["bid_done"].format(
                amount=value,
                civ=_civ_label(self.civ, _civ_names(wiring)),
                purse=lord.purse,
            ),
            ephemeral=True,
        )
        await refresh_panel_after_change_safe(interaction)


class KingdomMercenaryWithdrawButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:mercenary:withdraw",
):
    """The restart-proof bid-withdrawal button of the Chief's panel."""

    def __init__(self, label: str | None = None) -> None:
        super().__init__(
            discord.ui.Button(
                label=(label or "Retirer mon enchère")[:80],
                custom_id="kingdoms:mercenary:withdraw",
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
    ) -> KingdomMercenaryWithdrawButton:
        """Rebuild the withdraw button from the wire."""
        locale = str(interaction.locale) if interaction.locale else "fr"
        return cls(_strings(locale)["withdraw_button"])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Withdraw the lord's bid and refund the escrow."""
        from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

        locale = str(interaction.locale) if interaction.locale else "fr"
        strings = _strings(locale)
        wiring = _wiring()
        service = getattr(wiring, "mercenary_service", None)
        if service is None:
            await interaction.response.send_message(strings["no_service"], ephemeral=True)
            return
        try:
            withdrawn = await service.withdraw_bid(str(interaction.user.id))
        except Exception as exc:
            await interaction.response.send_message(_error_note(exc, strings), ephemeral=True)
            return
        if withdrawn is None:
            await interaction.response.send_message(strings["withdraw_none"], ephemeral=True)
            return
        _doc, lord = withdrawn
        await interaction.response.send_message(
            strings["withdraw_done"].format(purse=lord.purse),
            ephemeral=True,
        )
        await refresh_panel_after_change_safe(interaction)


async def refresh_panel_after_change_safe(interaction: discord.Interaction) -> None:
    """Re-render the Chief's panel after a bid/withdrawal (best effort)."""
    channel = getattr(interaction, "channel", None)
    if not isinstance(channel, discord.TextChannel):
        return
    locale = str(interaction.locale) if interaction.locale else "fr"
    try:
        await deploy_mercenary_panel(channel, locale)
    except Exception:
        logger.warning("KINGDOM MERCENARY: panel refresh failed", exc_info=True)


async def deploy_mercenary_panel(channel: discord.TextChannel, locale: str) -> bool:
    """Pin (or refresh in place) the Chief's panel in the taverne salon."""
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

    wiring = _wiring()
    doc = await _read_market_safe(wiring)
    content = build_mercenary_content(doc, locale)
    view: discord.ui.View | None = build_mercenary_view(doc, _civ_names(wiring))
    if not view.children:
        # the Chief is absent — no live component rides the message.
        view = None
    try:
        for message in await channel_messages(channel):
            if MERCENARY_PANEL_MARKER in message_text(message):
                try:
                    await message.edit(content=content, view=view)
                except Exception:
                    logger.warning(
                        "KINGDOM MERCENARY: panel edit failed, resending", exc_info=True
                    )
                    await channel.send(content=content, view=view, allowed_mentions=NO_MENTIONS)
                return True
        await channel.send(content=content, view=view, allowed_mentions=NO_MENTIONS)
        return True
    except Exception:
        logger.warning("KINGDOM MERCENARY: panel deploy failed", exc_info=True)
        return False


async def _read_market_safe(wiring: Any) -> dict[str, Any] | None:
    """Read the live market document (None on any failure)."""
    service = getattr(wiring, "mercenary_service", None)
    if service is None:
        return None
    try:
        doc = await service.market()
    except Exception:
        logger.warning("KINGDOM MERCENARY: market read failed", exc_info=True)
        return None
    if not isinstance(doc, dict):
        return None
    if doc.get("closed") and doc.get("date") != _paris_today():
        # a CLOSED market of another day is gone — the Chief left.
        return None
    return doc


def _paris_today() -> str:
    """Return today's date on the Chief's wall clock."""
    return datetime.now(UTC).astimezone(_TZ).strftime("%Y-%m-%d")


async def refresh_mercenary_panels_safe(guild: discord.Guild | None, locale: str) -> None:
    """Re-render the Chief's panel of a guild's taverne (best effort)."""
    from kingdoms.mods.kingdoms.kingdom_setup import _slug

    if guild is None:
        return
    for channel in getattr(guild, "channels", []):
        if _slug(str(getattr(channel, "name", ""))) != "taverne":
            continue
        try:
            await deploy_mercenary_panel(channel, locale)
        except Exception:
            logger.warning(
                "KINGDOM MERCENARY: guild panel refresh failed", exc_info=True
            )
        return
