"""Kingdoms Taverne panel — the 🍺 taverne salon surface (taverne-v1).

``TavernService`` owns the purse-and-drinks economy; this file is its
Discord surface: one pinned message in the ``🍺 taverne`` salon (born
with restructuration-gaia-v1) carrying one restart-proof button per
drink of the config catalog. The panel reads ONLY this module's FR/EN
strings dict (the per-module strings rule of the Kingdoms surfaces).

Tranche 1 (Drasah 24/10/2026): the pour flow — a click opens an
EPHEMERAL confirmation (price, lock, purse), ✅ pours the drink (the
purse is debited, the combat lock and the GLOBAL 24h cooldown are
posed) and answers with the live purse. The drink EFFECTS (rumor
window, FFA hour, forced map), the Renseignement archive, the bets and
the Mercenary Chief land with their own tranches.

The panel is deployed by the channel pass (``_deploy_channel_panel``,
slug ``taverne``) and refreshed in place through a marker — a
redeploy never duplicates the message.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import discord

from kingdoms.mods.kingdoms.panel_messages import NO_MENTIONS, channel_messages, message_text

logger = logging.getLogger("kingdoms.tavern_panel")

TAVERN_PANEL_MARKER = "<!-- kingdoms-tavern -->"
DEFAULT_TZ = "Europe/Paris"

_STRINGS: dict[str, dict[str, str]] = {
    "fr": {
        "panel_title": "🍺 La Taverne",
        "panel_intro": (
            "La salle commune des seigneurs — la bourse est personnelle, "
            "en **écus**, et ne se dépense qu'ici."
        ),
        "panel_drinks_header": "**Au comptoir**",
        "panel_rules": (
            "• Une boisson **verrouille le combat** comme une bataille en cours : "
            "ni déclarer, ni planifier, ni défendre, ni embuscade pendant le verrou.\n"
            "• **24h** minimum entre deux boissons, quelle qu'elles soient.\n"
            "• Chaque seigneur paie avec **sa propre bourse**."
        ),
        "drink_button_suffix": " · {price} écus",
        "confirm_title": "{label} — {price} écus · verrou {lock}",
        "confirm_body": (
            "Ta bourse : **{purse} écus**.\n"
            "Confirmer la commande ?"
        ),
        "confirm_yes": "✅ Commander",
        "confirm_no": "❌ Annuler",
        "cancelled": "Commande annulée — rien n'a été touché.",
        "poured": (
            "🥂 **{label}** consommé !\n"
            "• Verrou de combat jusqu'à **{end}**\n"
            "• Prochaine boisson possible : **{next}**\n"
            "• Bourse restante : **{purse} écus**"
        ),
        "no_service": "La Taverne n'est pas branchée sur cette instance.",
        "not_enrolled": "Tu n'es pas un seigneur actif de la saison en cours.",
        "season_closed": "La saison est terminée — la Taverne est fermée.",
        "cooldown": (
            "⏳ Anti-spam : tu ne peux reboire que dans **{remaining}** "
            "(24h entre deux boissons)."
        ),
        "insufficient": "🪙 Il te faut **{price} écus** — ta bourse n'en compte que {purse}.",
        "unknown_drink": "Cette boisson n'existe pas au comptoir.",
        "failed": "La commande a échoué : `{message}`.",
        "menu_button": "🍺 Demander à boire",
        "info_button": "🗣️ Demander un renseignement",
        "purse_button": "💰 Regarder sa bourse",
        "info_rp": (
            "Le tenancier essuie un gobelet sans lever les yeux, puis "
            "approche sa voix :\n"
            "« Ici, rien n'est gratuit, seigneur. Les renseignements "
            "coûtent cher… mais tu remarqueras que les langues ont "
            "tendance à se délier lorsqu'on boit. »"
        ),
        "info_header": "**Ce que chaque boisson fait réellement :**",
        "info_line": "• {label} — {effect} · verrou {time}",
        "drink_effect_verre": "ouvre une fenêtre d'écoute des rumeurs les plus proches",
        "drink_effect_vin": "révèle l'horaire des FFA",
        "drink_effect_alcool": "force la prochaine map",
        "drink_effect_generic": "les langues se délient",
        "purse_content": "Ta bourse contient **{purse} écu(s)**.",
    },
    "en": {
        "panel_title": "🍺 The Tavern",
        "panel_intro": (
            "The lords' common room — the purse is personal, in **écus**, "
            "and is spent only here."
        ),
        "panel_drinks_header": "**At the counter**",
        "panel_rules": (
            "• A drink **locks combat** like an ongoing battle: no declaring, "
            "no planning, no defending, no ambush while the lock runs.\n"
            "• **24h** minimum between two drinks, whatever they are.\n"
            "• Every lord pays from **his own purse**."
        ),
        "drink_button_suffix": " · {price} écus",
        "confirm_title": "{label} — {price} écus · lock {lock}",
        "confirm_body": (
            "Your purse: **{purse} écus**.\n"
            "Confirm the order?"
        ),
        "confirm_yes": "✅ Order",
        "confirm_no": "❌ Cancel",
        "cancelled": "Order cancelled — nothing was touched.",
        "poured": (
            "🥂 **{label}** enjoyed!\n"
            "• Combat lock until **{end}**\n"
            "• Next drink possible: **{next}**\n"
            "• Purse left: **{purse} écus**"
        ),
        "no_service": "The Tavern service is not wired on this instance.",
        "not_enrolled": "You are not an active lord of the current season.",
        "season_closed": "The season is over — the Tavern is closed.",
        "cooldown": (
            "⏳ Anti-spam: you may drink again in **{remaining}** "
            "(24h between two drinks)."
        ),
        "insufficient": "🪙 You need **{price} écus** — your purse only holds {purse}.",
        "unknown_drink": "This drink does not exist at the counter.",
        "failed": "The order failed: `{message}`.",
        "menu_button": "🍺 Ask for a drink",
        "info_button": "🗣️ Ask for information",
        "purse_button": "💰 Check your purse",
        "info_rp": (
            "The barkeep wipes a tankard without looking up, then "
            "lowers his voice:\n"
            "\"Nothing comes free here, my lord. Information costs "
            "dear… though you may notice tongues tend to loosen over "
            "a drink.\""
        ),
        "info_header": "**What each drink really does:**",
        "info_line": "• {label} — {effect} · lock {time}",
        "drink_effect_verre": "opens a listening window on the closest rumors",
        "drink_effect_vin": "reveals the FFA schedule",
        "drink_effect_alcool": "forces the next map",
        "drink_effect_generic": "tongues loosen",
        "purse_content": "Your purse holds **{purse} écu(s)**.",
    },
}


def _strings(locale: str) -> dict[str, str]:
    """Return the module's strings of a locale (FR default)."""
    return _STRINGS["fr"] if str(locale).lower().startswith("fr") else _STRINGS["en"]


def _fmt_paris(timestamp: int) -> str:
    """Format a unix timestamp on the players' wall clock (Paris)."""
    moment = datetime.fromtimestamp(timestamp, tz=ZoneInfo(DEFAULT_TZ))
    return f"{moment:%Hh%M}"


def _fmt_remaining(seconds: int) -> str:
    """Format a remaining delay as « 23h04 » / « 42 min »."""
    minutes = max(1, round(seconds / 60))
    if minutes >= 60:
        return f"{minutes // 60}h{minutes % 60:02d}"
    return f"{minutes} min"


def _drink_catalog(wiring: Any) -> tuple[Any, ...]:
    """Return the config drink catalog (empty when the wiring is cold)."""
    service = getattr(wiring, "kingdoms_service", None)
    config = getattr(service, "config", None) if service is not None else None
    return tuple(getattr(getattr(config, "tavern", None), "drinks", ()) or ())


def build_info_content(wiring: Any, locale: str) -> str:
    """The tenancier's ephemeral: RP intro, then the plain-truth
    per-drink effects (taverne-comptoir-v3, Drasah 26/10)."""
    strings = _strings(locale)
    lines: list[str] = [strings["info_rp"], "", strings["info_header"]]
    for drink in _drink_catalog(wiring):
        effect = strings.get(
            f"drink_effect_{drink.key}", strings["drink_effect_generic"]
        )
        lines.append(
            strings["info_line"].format(
                label=drink.label,
                effect=effect,
                time=_fmt_remaining(int(drink.lock_minutes) * 60),
            )
        )
    return "\n".join(lines)


class KingdomTavernInfoButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template="kingdoms:tavern:info",
):
    """The comptoir pin's « Demander un renseignement » — FIRST button,
    the tenancier's ephemeral answer (RP + plain drink effects)."""

    def __init__(self, label: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                custom_id="kingdoms:tavern:info",
                style=discord.ButtonStyle.secondary,
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: Any,
        /,
    ) -> KingdomTavernInfoButton:
        locale = str(interaction.locale) if interaction.locale else "fr"
        return cls(_strings(locale)["info_button"])

    async def callback(self, interaction: discord.Interaction) -> None:
        from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

        locale = str(interaction.locale) if interaction.locale else "fr"
        wiring = _wiring()
        try:
            await interaction.response.send_message(
                build_info_content(wiring, locale), ephemeral=True
            )
        except Exception:
            logger.warning("KINGDOM TAVERN: info ephemeral failed", exc_info=True)


class KingdomTavernPurseButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template="kingdoms:tavern:purse",
):
    """The comptoir pin's « Regarder sa bourse » — apart from the RP
    row (its own button row below), shows the lord's écus."""

    def __init__(self, label: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                custom_id="kingdoms:tavern:purse",
                style=discord.ButtonStyle.secondary,
                row=1,
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: Any,
        /,
    ) -> KingdomTavernPurseButton:
        locale = str(interaction.locale) if interaction.locale else "fr"
        return cls(_strings(locale)["purse_button"])

    async def callback(self, interaction: discord.Interaction) -> None:
        from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

        locale = str(interaction.locale) if interaction.locale else "fr"
        strings = _strings(locale)
        wiring = _wiring()
        service = getattr(wiring, "tavern_service", None)
        if service is None:
            await interaction.response.send_message(strings["no_service"], ephemeral=True)
            return
        try:
            lord = await service.lord(str(interaction.user.id))
        except Exception as exc:
            await interaction.response.send_message(_error_note(exc, strings), ephemeral=True)
            return
        await interaction.response.send_message(
            strings["purse_content"].format(purse=lord.purse), ephemeral=True
        )


class KingdomTavernMenuButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template="kingdoms:tavern:menu",
):
    """The comptoir pin's « Demander à boire » — opens the EPHEMERAL
    drinks interface (taverne-comptoir-v2, Drasah 26/10): the pinned
    drinks message of the comptoir is gone, the drinks live behind
    this button (one ephemeral view, drink buttons inside)."""

    def __init__(self, label: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                custom_id="kingdoms:tavern:menu",
                style=discord.ButtonStyle.primary,
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: Any,
        /,
    ) -> KingdomTavernMenuButton:
        locale = str(interaction.locale) if interaction.locale else "fr"
        return cls(_strings(locale)["menu_button"])

    async def callback(self, interaction: discord.Interaction) -> None:
        from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

        locale = str(interaction.locale) if interaction.locale else "fr"
        wiring = _wiring()
        try:
            await interaction.response.send_message(
                build_tavern_content(locale),
                view=build_tavern_view(wiring, locale),
                ephemeral=True,
            )
        except Exception:
            logger.warning("KINGDOM TAVERN: menu open failed", exc_info=True)


class KingdomTavernDrinkButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:tavern:drink:(?P<drink>[a-z_]+)",
):
    """The restart-proof order button of one Taverne drink."""

    def __init__(self, drink: Any, label: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                custom_id=f"kingdoms:tavern:drink:{drink.key}",
                style=discord.ButtonStyle.primary,
            )
        )
        self.drink = drink

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomTavernDrinkButton:
        """Rebuild the drink button from the wire."""
        from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

        wiring = _wiring()
        drink = next(
            (entry for entry in _drink_catalog(wiring) if entry.key == match.group("drink")),
            None,
        )
        if drink is None:
            drink = _CatalogGhost(match.group("drink"))
        label = f"{drink.label} · {drink.price} écus"
        return cls(drink, label)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the ephemeral pour confirmation of this drink."""
        from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

        locale = str(interaction.locale) if interaction.locale else "fr"
        strings = _strings(locale)
        wiring = _wiring()
        service = getattr(wiring, "tavern_service", None)
        if service is None:
            await interaction.response.send_message(strings["no_service"], ephemeral=True)
            return
        try:
            lord = await service.lord(str(interaction.user.id))
        except Exception as exc:
            await interaction.response.send_message(_error_note(exc, strings), ephemeral=True)
            return
        price = int(self.drink.price)
        lock_minutes = int(self.drink.lock_minutes)
        await interaction.response.send_message(
            strings["confirm_title"].format(
                label=self.drink.label,
                price=price,
                lock=_fmt_remaining(lock_minutes * 60),
            )
            + "\n"
            + strings["confirm_body"].format(purse=lord.purse),
            view=_PourConfirmView(self.drink, strings, wiring),
            ephemeral=True,
        )


class _CatalogGhost:
    """A ghost entry for an unknown wire key (config changed mid-flight)."""

    def __init__(self, key: str) -> None:
        self.key = key
        self.label = f"🍻 {key}"
        self.price = 0
        self.lock_minutes = 0


class _PourConfirmView(discord.ui.View):
    """The ephemeral ✅/❌ confirmation of one pour (tranche 1)."""

    def __init__(self, drink: Any, strings: dict[str, str], wiring: Any) -> None:
        super().__init__(timeout=300)
        self.drink = drink
        self.strings = strings
        self.wiring = wiring
        self.add_item(_PourYesButton(self))
        self.add_item(_PourNoButton(self))

    async def confirm(self, interaction: discord.Interaction) -> None:
        """Pour the drink and answer with the live purse."""
        service = getattr(self.wiring, "tavern_service", None)
        if service is None:
            await interaction.response.edit_message(
                content=self.strings["no_service"], view=None
            )
            return
        try:
            lord, drink = await service.buy_drink(
                str(interaction.user.id), str(self.drink.key)
            )
        except Exception as exc:
            await interaction.response.edit_message(
                content=_error_note(exc, self.strings), view=None
            )
            return
        await interaction.response.edit_message(
            content=self.strings["poured"].format(
                label=drink.label,
                end=_fmt_paris(int(lord.tavern_locked_until or 0)),
                next=_fmt_paris(int(lord.tavern_next_drink_at or 0)),
                purse=lord.purse,
            ),
            view=None,
        )

    async def cancel(self, interaction: discord.Interaction) -> None:
        """Abort the pour — nothing was touched."""
        await interaction.response.edit_message(content=self.strings["cancelled"], view=None)


class _PourYesButton(discord.ui.Button[Any]):
    """The ✅ of the pour confirmation — pours the drink."""

    def __init__(self, parent: _PourConfirmView) -> None:
        super().__init__(
            label=parent.strings["confirm_yes"],
            style=discord.ButtonStyle.success,
        )
        self.parent = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.parent.confirm(interaction)


class _PourNoButton(discord.ui.Button[Any]):
    """The ❌ of the pour confirmation — aborts untouched."""

    def __init__(self, parent: _PourConfirmView) -> None:
        super().__init__(
            label=parent.strings["confirm_no"],
            style=discord.ButtonStyle.secondary,
        )
        self.parent = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.parent.cancel(interaction)


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
    from kingdoms.mods.kingdoms.game_clock import game_now
    from kingdoms.mods.kingdoms.attacks import NotEnrolledError
    from kingdoms.mods.kingdoms.tavern import (
        TavernCooldownError,
        TavernDrinkNotFoundError,
        TavernInsufficientPurseError,
        TavernSeasonClosedError,
    )

    if isinstance(exc, TavernCooldownError):
        next_at = int(_error_context(exc).get("next_at") or 0)
        remaining = max(0, next_at - int(game_now().timestamp()))
        return strings["cooldown"].format(remaining=_fmt_remaining(remaining))
    if isinstance(exc, TavernInsufficientPurseError):
        context = _error_context(exc)
        return strings["insufficient"].format(
            price=context.get("price", "?"),
            purse=context.get("purse", "?"),
        )
    if isinstance(exc, TavernDrinkNotFoundError):
        return strings["unknown_drink"]
    if isinstance(exc, TavernSeasonClosedError):
        return strings["season_closed"]
    if isinstance(exc, NotEnrolledError):
        return strings["not_enrolled"]
    return strings["failed"].format(message=getattr(exc, "message", None) or str(exc))


def build_tavern_view(wiring: Any, locale: str) -> discord.ui.View:
    """Build the pinned Taverne view (one button per catalog drink)."""
    strings = _strings(locale)
    view = discord.ui.View(timeout=None)
    for drink in _drink_catalog(wiring):
        view.add_item(
            KingdomTavernDrinkButton(
                drink,
                f"{drink.label}"
                + strings["drink_button_suffix"].format(price=int(drink.price)),
            )
        )
    return view


def build_tavern_content(locale: str) -> str:
    """Build the pinned Taverne message content (marker + rules)."""
    strings = _strings(locale)
    return (
        f"{TAVERN_PANEL_MARKER}\n"
        f"**{strings['panel_title']}**\n"
        f"{strings['panel_intro']}\n\n{strings['panel_rules']}"
    )


async def deploy_tavern_panel(channel: discord.TextChannel, locale: str) -> bool:
    """Pin (or refresh in place) the Taverne panel in its salon."""
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

    wiring = _wiring()
    content = build_tavern_content(locale)
    view = build_tavern_view(wiring, locale)
    try:
        for message in await channel_messages(channel):
            if TAVERN_PANEL_MARKER in message_text(message):
                try:
                    await message.edit(content=content, view=view)
                except Exception:
                    logger.warning(
                        "KINGDOM TAVERN: panel edit failed, resending", exc_info=True
                    )
                    await channel.send(content=content, view=view, allowed_mentions=NO_MENTIONS)
                return True
        await channel.send(content=content, view=view, allowed_mentions=NO_MENTIONS)
        return True
    except Exception:
        logger.warning("KINGDOM TAVERN: panel deploy failed", exc_info=True)
        return False
