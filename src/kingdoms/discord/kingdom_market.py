"""The Marché panel (kingdoms#161): the Roi-only technology boutique.

Product decisions (Drasah, session of 2026-10-07):

- one persistent panel pinned in the Marché salon of the Kingdoms
  structure — no slash command, everything rides the buttons;
- the shop sells the technologies only (combat technologies and the
  special actions Explorateur / Corruption / Garde Royale);
- purchases are reserved to the Roi of a kingdom: a Lord, an unenrolled
  admin or a spectator answers with a note, nothing is bought.

The wallet display and the buy buttons are rebuilt from the live season
state at every deployment (the panel message carries a stable marker so
the next deploy replaces it instead of stacking copies). The purchase
buttons are persistent DynamicItems: their state rides the custom_id
(``kingdoms:market:buy:<tech>``) and the class is re-registered at every
startup, so the panel survives restarts (ADR-0009).
"""

from __future__ import annotations

import logging
import re
from typing import Any

import discord

logger = logging.getLogger("kingdoms.kingdom_market")

MARKET_CHANNEL_SLUG = "marche"
MARKET_PANEL_MARKER = "kingdoms:panel:marche"
MARKET_BUTTON_PREFIX = "kingdoms:market:buy"

# The special actions need a target (a map key or a territory id) typed
# into a modal; the combat technologies are bought with a single click.
TARGET_ACTIONS: dict[str, str] = {
    "explorateur": "map",
    "corruption": "territory",
    "garde_royale": "territory",
}

MARKET_TECHNOLOGIES: tuple[str, ...] = (
    "embuscade",
    "traquenard",
    "patrouille",
    "contre_espionnage",
    "sabotage",
    "jeu_d_armes",
    "mariage_arrange",
    "explorateur",
    "corruption",
    "garde_royale",
)

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "market_title": "🏪 Kingdoms — market",
        "market_wallets": "Bank of each kingdom",
        "market_shop": "Technologies — the King buys for the kingdom",
        "market_footer": "Only the King of a kingdom can buy. One shared wallet per kingdom.",
        "market_no_service": "The market is closed right now (no season service).",
        "market_king_only": "Only the King of a kingdom can buy here.",
        "market_failed": "Purchase refused ({}).",
        "market_bought": "Purchased: **{}** ({} 🔬).",
        "target_title": "🎯 {}",
        "target_label_map": "Map to explore (map key)",
        "target_label_territory": "Territory id",
        "target_placeholder_map": "arabia",
        "target_placeholder_territory": "t-…",
        "target_missing": "A target is required.",
    },
    "fr": {
        "market_title": "🏪 Kingdoms — marché",
        "market_wallets": "Trésorerie de chaque royaume",
        "market_shop": "Technologies — le Roi achète pour le royaume",
        "market_footer": "Seul le Roi d'un royaume peut acheter. Une caisse unique par royaume.",
        "market_no_service": "Le marché est fermé pour le moment (service indisponible).",
        "market_king_only": "Seul le Roi d'un royaume peut acheter ici.",
        "market_failed": "Achat refusé ({}).",
        "market_bought": "Acheté : **{}** ({} 🔬).",
        "target_title": "🎯 {}",
        "target_label_map": "Carte à explorer (clé de carte)",
        "target_label_territory": "Identifiant du territoire",
        "target_placeholder_map": "arabia",
        "target_placeholder_territory": "t-…",
        "target_missing": "Une cible est obligatoire.",
    },
}


def _strings(locale: str) -> dict[str, str]:
    """Return the market strings for a locale (English fallback)."""
    return STRINGS["fr"] if str(locale).lower().startswith("fr") else STRINGS["en"]


def _tech_label(tech: str, locale: str) -> str:
    """Resolve one technology display name through the snapshot catalog."""
    from kingdoms.mods.kingdoms.snapshot import DEFAULT_LOCALE, TECHNOLOGY_NAMES

    names = TECHNOLOGY_NAMES.get(tech, {})
    return names.get(locale, names.get(DEFAULT_LOCALE, tech))


def _tech_cost(config: Any, tech: str) -> int:
    """Read one technology cost from the season configuration."""
    cost = getattr(config.technologies, tech, 0)
    return cost if isinstance(cost, int) else 0


def _market_wiring() -> Any:
    """Resolve the panel wiring (kingdoms + economy services) at click time."""
    from kingdoms.discord.kingdom_persistent import _wiring

    return _wiring()


def _locale_of(interaction: discord.Interaction) -> str:
    """Resolve the interaction locale with an English fallback."""
    return str(interaction.locale) if interaction.locale else "en"


async def _resolve_king(kingdoms_service: Any, player_id: str) -> Any:
    """Return the active King lord document of a player, or None."""
    lords = await kingdoms_service.lords()
    for lord in lords:
        if lord.id == player_id and not lord.left and lord.role.value == "king":
            return lord if lord.kingdom_id else None
    return None


async def _buy(economy: Any, tech: str, kingdom_id: str, target: str) -> Any:
    """Run one purchase through the economy service (target-aware)."""
    if tech == "explorateur":
        return await economy.buy_explorateur(kingdom_id, target)
    if tech == "corruption":
        return await economy.buy_corruption(kingdom_id, target)
    if tech == "garde_royale":
        return await economy.buy_royal_guard(kingdom_id, target)
    return await economy.buy_combat_technology(kingdom_id, tech)


class _MarketTargetModal(discord.ui.Modal):
    """The Roi's target form for a targeted special action."""

    def __init__(self, tech: str, kingdom_id: str, locale: str) -> None:
        """Store the purchase context; the field label rides the action kind."""
        self._tech = tech
        self._kingdom_id = kingdom_id
        self._locale = locale
        strings = _strings(locale)
        kind = TARGET_ACTIONS.get(tech, "territory")
        super().__init__(
            title=strings["target_title"].format(_tech_label(tech, locale))[:45],
            timeout=None,
        )
        self.target = discord.ui.TextInput(
            label=strings[f"target_label_{kind}"][:45],
            placeholder=strings[f"target_placeholder_{kind}"][:100],
            required=True,
            min_length=1,
            max_length=100,
        )
        self.add_item(self.target)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Debit the wallet, then answer with the purchase outcome."""
        strings = _strings(self._locale)
        wiring = _market_wiring()
        economy = wiring.economy_service
        if economy is None:
            await interaction.response.send_message(strings["market_no_service"], ephemeral=True)
            return
        target = str(self.target.value or "").strip()
        if not target:
            await interaction.response.send_message(strings["target_missing"], ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await _buy(economy, self._tech, self._kingdom_id, target)
        except Exception as exc:
            logger.info("KINGDOM MARKET: purchase failed (%s)", type(exc).__name__)
            await interaction.followup.send(strings["market_failed"].format(type(exc).__name__), ephemeral=True)
            return
        await interaction.followup.send(
            strings["market_bought"].format(_tech_label(self._tech, self._locale), target), ephemeral=True
        )


class KingdomMarketButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:market:buy:(?P<tech>[a-z_]+)",
):
    """The restart-proof buy button of one technology of the Marché panel."""

    def __init__(self, tech: str, label: str) -> None:
        """Build one persistent button; the tech rides the custom_id."""
        super().__init__(
            discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.primary,
                custom_id=f"{MARKET_BUTTON_PREFIX}:{tech}",
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
    ) -> KingdomMarketButton:
        """Rebuild the buy button from the wire."""
        tech = match.group("tech")
        locale = _locale_of(interaction)
        label = f"{_tech_label(tech, locale)}"[:80]
        return cls(tech, label)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Guard the Roi rule, then buy or open the target modal."""
        wiring = _market_wiring()
        kingdoms_service = wiring.kingdoms_service
        locale = _locale_of(interaction)
        strings = _strings(locale)
        if kingdoms_service is None or wiring.economy_service is None:
            await interaction.response.send_message(strings["market_no_service"], ephemeral=True)
            return
        try:
            king = await _resolve_king(kingdoms_service, str(interaction.user.id))
        except Exception:
            logger.warning("KINGDOM MARKET: king resolution failed", exc_info=True)
            king = None
        if king is None:
            await interaction.response.send_message(strings["market_king_only"], ephemeral=True)
            return
        if self.tech in TARGET_ACTIONS:
            await interaction.response.send_modal(_MarketTargetModal(self.tech, king.kingdom_id, locale))
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await _buy(wiring.economy_service, self.tech, king.kingdom_id, "")
        except Exception as exc:
            logger.info("KINGDOM MARKET: purchase failed (%s)", type(exc).__name__)
            await interaction.followup.send(
                strings["market_failed"].format(type(exc).__name__), ephemeral=True
            )
            return
        cost = _tech_cost(kingdoms_service.config, self.tech)
        await interaction.followup.send(
            strings["market_bought"].format(_tech_label(self.tech, locale), cost), ephemeral=True
        )


async def _wallet_lines(kingdoms_service: Any) -> list[str]:
    """One line per player kingdom with its tech-point bank."""
    kingdoms = await kingdoms_service.kingdoms()
    return [
        f"🏛️ **{kingdom.name}** — {kingdom.tech_points_bank} 🔬"
        for kingdom in kingdoms
        if not kingdom.is_gaia
    ]


def _shop_row(techs: tuple[str, ...], locale: str, config: Any) -> discord.ui.ActionRow[discord.ui.LayoutView]:
    """One action row of persistent buy buttons (five buttons max)."""
    row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    for tech in techs:
        label = f"{_tech_label(tech, locale)} ({_tech_cost(config, tech)}🔬)"[:80]
        row.add_item(KingdomMarketButton(tech, label))
    return row


async def build_market_panel(kingdoms_service: Any, locale: str) -> discord.ui.LayoutView:
    """Build the persistent Marché panel for the current season state."""
    strings = _strings(locale)
    container = discord.ui.Container(accent_colour=discord.Colour.gold())
    container.add_item(discord.ui.TextDisplay(f"# {strings['market_title']}"))
    wallets: list[str] = []
    if kingdoms_service is not None:
        try:
            wallets = await _wallet_lines(kingdoms_service)
        except Exception:
            logger.warning("KINGDOM MARKET: wallet read failed", exc_info=True)
    container.add_item(discord.ui.Separator())
    container.add_item(discord.ui.TextDisplay(f"## 🔬 {strings['market_wallets']}"))
    container.add_item(discord.ui.TextDisplay("\n".join(wallets) if wallets else "—"))
    container.add_item(discord.ui.Separator())
    container.add_item(discord.ui.TextDisplay(f"## 🛒 {strings['market_shop']}"))
    config = getattr(kingdoms_service, "config", None)
    if config is not None:
        container.add_item(_shop_row(MARKET_TECHNOLOGIES[:5], locale, config))
        container.add_item(_shop_row(MARKET_TECHNOLOGIES[5:], locale, config))
    container.add_item(discord.ui.Separator())
    container.add_item(discord.ui.TextDisplay(f"-# {strings['market_footer']}"))
    container.add_item(discord.ui.TextDisplay(f"-# {MARKET_PANEL_MARKER}"))
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(container)
    return view


async def deploy_market_panel(
    channel: discord.TextChannel,
    kingdoms_service: Any,
    locale: str,
) -> bool:
    """Pin a fresh market panel, replacing the previous marked message."""
    for message in list(getattr(channel, "messages", [])):
        stale = (
            MARKET_PANEL_MARKER in (message.content or "")
            or (
                getattr(message, "author", None) is not None
                and bool(getattr(message.author, "bot", False))
                and getattr(message, "view", None) is not None
            )
        )
        if stale:
            try:
                await message.delete()
            except Exception:
                logger.warning("KINGDOM MARKET: stale panel delete failed", exc_info=True)
    view = await build_market_panel(kingdoms_service, locale)
    await channel.send(view=view)
    return True
