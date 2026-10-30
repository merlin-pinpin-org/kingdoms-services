"""Kingdoms distributor panel — the Distributeur salon (D75).

Drasah's test injector (2026-10-12): one pinned message in the admin
``distributeur`` salon opens an EPHEMERAL three-click flow — pick a
kingdom, pick a category, inject (or remove) — and every mutation
re-renders the kingdom's live salons so the admin watches the element
propagate into the game.

The flow is session-scoped (ephemeral views with the kingdom kept in
memory between two injections); only the opening button is a
persistent ``DynamicItem`` riding ``kingdoms:distributor:open`` — its
labels come from THIS module's strings (the Check 30 lesson: a
persistent item rebuilt from the wire must find every key in the
strings its ``from_custom_id`` reads, or the click stays mute).
"""

from __future__ import annotations

import logging
import re
from typing import Any

import discord

logger = logging.getLogger("kingdoms.distributor_panel")

DISTRIBUTOR_PANEL_MARKER = "<!-- kingdoms-distributor -->"

PAGE_SIZE = 25
"""Discord's select-option ceiling — the civ catalog is paginated."""

_STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "panel_title": "🎁 **Kingdoms distributor** — inject test elements",
        "panel_help": (
            "Pick a kingdom, then hand it civilizations, tech points, "
            "territories… and watch them appear live in its salons.\n"
            "No costs, no limits — only the alliance rules bind (never a "
            "duplicate of the hand, never a married civilization)."
        ),
        "open_button": "🏰 Open the distributor",
        "not_allowed": "The distributor is admin-only.",
        "no_service": "The distributor service is not wired.",
        "failed": "The distributor action failed: `{0}`.",
        "choose_kingdom": "🏰 **Pick a kingdom** — the flow then keeps it in memory.",
        "no_kingdoms": "No kingdom exists yet — launch a season and create kingdoms first.",
        "back_button": "🔙 Kingdom",
        "menu_title": "**{kingdom}** — pick a category:",
        "cat_alliance_add": "+ Alliance",
        "cat_alliance_remove": "- Alliance",
        "cat_treasury": "🔬 Treasury",
        "cat_parish": "💒 Parish",
        "cat_territories": "🗺️ Territories",
        "cat_lord": "👤 Lord",
        "cat_tech": "⚔️ Tech",
        "cat_status": "🧾 Status",
        "civ_add_title": "+ **{kingdom}** — hand a civilization (page {page}/{pages}):",
        "civ_remove_title": "- **{kingdom}** — remove a civilization (page {page}/{pages}):",
        "civ_status_free": "✅ available",
        "civ_status_hand": "⏹ already in the hand",
        "civ_status_married": "💍 married — {lord}",
        "civ_secured": "💍 secured by a marriage of this kingdom",
        "civ_add_done": "✅ **{kingdom}** now holds **{civ}** in Alliances.",
        "civ_remove_done": "✅ **{civ}** removed from **{kingdom}**'s hand.",
        "prev_page": "◀",
        "next_page": "▶",
        "treasury_title": "🔬 **{kingdom}** — tech treasury: **{wallet}**",
        "treasury_set": "📝 Set…",
        "value_modal_title": "Set the value",
        "value_modal_label": "New value (integer ≥ 0)",
        "value_modal_bad": "That is not a valid integer ≥ 0.",
        "value_set_done": "✅ {field} = **{value}**.",
        "parish_title": "💒 **{kingdom}** — {tier} · points **{points}** · tokens **{tokens}**",
        "parish_tier_set": "✅ Parish forced to **{tier}**.",
        "points_button": "💍 point",
        "token_button": "🎫 token",
        "parish_done": "✅ **{kingdom}** · {field}: **{value}**",
        "terr_title": "🗺️ **{kingdom}** — {count} territory(ies):",
        "terr_line": "· {map} {shield}",
        "terr_shielded": "🛡",
        "terr_give_button": "+ Give a map",
        "terr_transfer_button": "🔁 Transfer",
        "terr_retire_button": "- Retire",
        "terr_replace_button": "♻ Replace",
        "terr_shield_button": "🛡 Shield 48h",
        "terr_unshield_button": "🔓 Clear shield",
        "terr_pick_line": "**{kingdom}** — pick a territory:",
        "terr_give_pick_line": "Pick a map still available this season:",
        "terr_pick_kingdom_line": "Transfer **{map}** to which kingdom?",
        "terr_none": "No territory to show here.",
        "terr_maps_none": "Every catalog map is already out this season.",
        "terr_give_done": "✅ **{map}** attributed to **{kingdom}**.",
        "terr_transfer_done": "✅ **{map}** transferred to **{kingdom}**.",
        "terr_retire_done": "✅ **{map}** retired (out for the season).",
        "terr_replace_done": "♻ **{map}** ejected — replacement drawn.",
        "terr_shield_done": "🛡 **{map}** shielded for 48h.",
        "terr_unshield_done": "🔓 Shield cleared on **{map}**.",
        "lord_title": "👤 **{kingdom}** — pick a lord:",
        "lord_line": "{role} · budgets {attack}/{defense}",
        "lord_role_king": "👑",
        "lord_role_lord": "🎖️",
        "lord_none": "No active lord in this kingdom.",
        "lord_action_title": "**{lord}** ({kingdom}):",
        "lord_budgets_button": "↻ Recharge budgets",
        "lord_unlock_button": "🔓 Clear marriage lock",
        "lord_lock_button": "🔒 Marriage lock 6h",
        "lord_budgets_done": "✅ Budgets of **{lord}** recharged (1 attack / 1 defense).",
        "lord_unlock_done": "🔓 Marriage lock cleared on **{lord}**.",
        "lord_lock_done": "🔒 **{lord}** locked out of combat for 6h.",
        "tech_title": "⚔️ **{kingdom}** — pick a combat technology:",
        "tech_line": "purchases: {count}",
        "tech_action_title": "**{tech}** — purchases: **{count}**",
        "tech_plus_button": "✅ +1 (force)",
        "tech_minus_button": "❌ -1",
        "tech_done": "✅ **{tech}** of **{kingdom}** → **{count}** purchase(s).",
        "status_title": "🧾 **{kingdom}** — {validation}",
        "status_parish": "💒 {tier} · 🔬 **{tech}** · 💒 points **{points}** · 🎫 tokens **{tokens}**",
        "status_hand": "🤝 Hand ({count}): {civs}",
        "status_secured": "💍 Secured: {civs}",
        "status_lord": "{role} **{name}** — 💍 {marriage} · 🗡 {attack} ⚔️ {defense} {lock}",
        "status_married_none": "single",
        "status_lock_line": " · 🔒 until {until}",
        "status_territories": "🗺️ Territories ({count}): {maps}",
        "status_purchases": "⚔️ Tech: {purchases}",
        "status_purchases_none": "⚔️ Tech: none",
    },
    "fr": {
        "panel_title": "🎁 **Distributeur Kingdoms** — injectez des éléments de test",
        "panel_help": (
            "Choisissez un royaume, puis attribuez-lui des civilisations, "
            "des points 🔬, des territoires… et regardez-les apparaître en "
            "direct dans ses salons.\n"
            "Pas de coûts ni de limites — seules les règles d'alliance "
            "s'appliquent (jamais de doublon dans la main, jamais une civ "
            "déjà mariée)."
        ),
        "open_button": "🏰 Ouvrir le distributeur",
        "not_allowed": "Le distributeur est réservé aux admins.",
        "no_service": "Le service distributeur n'est pas câblé.",
        "failed": "L'action distributeur a échoué : `{0}`.",
        "choose_kingdom": "🏰 **Choisissez un royaume** — le flow le garde ensuite en mémoire.",
        "no_kingdoms": "Aucun royaume n'existe encore — lancez une saison et créez des royaumes d'abord.",
        "back_button": "🔙 Royaume",
        "menu_title": "**{kingdom}** — choisissez une catégorie :",
        "cat_alliance_add": "+ Alliance",
        "cat_alliance_remove": "- Alliance",
        "cat_treasury": "🔬 Trésor",
        "cat_parish": "💒 Paroisse",
        "cat_territories": "🗺️ Territoires",
        "cat_lord": "👤 Seigneur",
        "cat_tech": "⚔️ Tech",
        "cat_status": "🧾 Statut",
        "civ_add_title": "+ **{kingdom}** — attribuer une civilisation (page {page}/{pages}) :",
        "civ_remove_title": "- **{kingdom}** — retirer une civilisation (page {page}/{pages}) :",
        "civ_status_free": "✅ disponible",
        "civ_status_hand": "⏹ déjà dans la main",
        "civ_status_married": "💍 mariée — {lord}",
        "civ_secured": "💍 sécurisée par un mariage de ce royaume",
        "civ_add_done": "✅ **{kingdom}** détient désormais **{civ}** en Alliances.",
        "civ_remove_done": "✅ **{civ}** retirée de la main de **{kingdom}**.",
        "prev_page": "◀",
        "next_page": "▶",
        "treasury_title": "🔬 **{kingdom}** — trésor : **{wallet}**",
        "treasury_set": "📝 Définir…",
        "value_modal_title": "Définir la valeur",
        "value_modal_label": "Nouvelle valeur (entier ≥ 0)",
        "value_modal_bad": "Ce n'est pas un entier valide ≥ 0.",
        "value_set_done": "✅ {field} = **{value}**.",
        "parish_title": "💒 **{kingdom}** — {tier} · points **{points}** · jetons **{tokens}**",
        "parish_tier_set": "✅ Paroisse forcée à **{tier}**.",
        "points_button": "💍 point",
        "token_button": "🎫 jeton",
        "parish_done": "✅ **{kingdom}** · {field} : **{value}**",
        "terr_title": "🗺️ **{kingdom}** — {count} territoire(s) :",
        "terr_line": "· {map} {shield}",
        "terr_shielded": "🛡",
        "terr_give_button": "+ Attribuer une carte",
        "terr_transfer_button": "🔁 Transférer",
        "terr_retire_button": "- Retirer",
        "terr_replace_button": "♻ Remplacer",
        "terr_shield_button": "🛡 Bouclier 48h",
        "terr_unshield_button": "🔓 Lever bouclier",
        "terr_pick_line": "**{kingdom}** — choisissez un territoire :",
        "terr_give_pick_line": "Choisissez une carte encore disponible cette saison :",
        "terr_pick_kingdom_line": "Transférer **{map}** vers quel royaume ?",
        "terr_none": "Aucun territoire à afficher ici.",
        "terr_maps_none": "Toutes les cartes du catalogue sont déjà sorties cette saison.",
        "terr_give_done": "✅ **{map}** attribuée à **{kingdom}**.",
        "terr_transfer_done": "✅ **{map}** transférée à **{kingdom}**.",
        "terr_retire_done": "✅ **{map}** retirée (hors saison).",
        "terr_replace_done": "♻ **{map}** éjectée — remplacement tiré.",
        "terr_shield_done": "🛡 **{map}** protégée 48h.",
        "terr_unshield_done": "🔓 Bouclier levé sur **{map}**.",
        "lord_title": "👤 **{kingdom}** — choisissez un seigneur :",
        "lord_line": "{role} · budgets {attack}/{defense}",
        "lord_role_king": "👑",
        "lord_role_lord": "🎖️",
        "lord_none": "Aucun seigneur actif dans ce royaume.",
        "lord_action_title": "**{lord}** ({kingdom}) :",
        "lord_budgets_button": "↻ Recharger les budgets",
        "lord_unlock_button": "🔓 Lever le verrou mariage",
        "lord_lock_button": "🔒 Verrou mariage 6h",
        "lord_budgets_done": "✅ Budgets de **{lord}** rechargés (1 attaque / 1 défense).",
        "lord_unlock_done": "🔓 Verrou mariage levé sur **{lord}**.",
        "lord_lock_done": "🔒 **{lord}** verrouillé hors combat pour 6h.",
        "tech_title": "⚔️ **{kingdom}** — choisissez une technologie de combat :",
        "tech_line": "achats : {count}",
        "tech_action_title": "**{tech}** — achats : **{count}**",
        "tech_plus_button": "✅ +1 (forcer)",
        "tech_minus_button": "❌ -1",
        "tech_done": "✅ **{tech}** de **{kingdom}** → **{count}** achat(s).",
        "status_title": "🧾 **{kingdom}** — {validation}",
        "status_parish": "💒 {tier} · 🔬 **{tech}** · 💒 points **{points}** · 🎫 jetons **{tokens}**",
        "status_hand": "🤝 Main ({count}) : {civs}",
        "status_secured": "💍 Sécurisées : {civs}",
        "status_lord": "{role} **{name}** — 💍 {marriage} · 🗡 {attack} ⚔️ {defense} {lock}",
        "status_married_none": "célibataire",
        "status_lock_line": " · 🔒 jusqu'à {until}",
        "status_territories": "🗺️ Territoires ({count}) : {maps}",
        "status_purchases": "⚔️ Tech : {purchases}",
        "status_purchases_none": "⚔️ Tech : aucune",
    },
}


def _strings(locale: str) -> dict[str, str]:
    return _STRINGS["fr"] if str(locale).lower().startswith("fr") else _STRINGS["en"]


def _locale(interaction: discord.Interaction) -> str:
    return str(interaction.locale) if interaction.locale else "fr"


def panel_text(strings: dict[str, str]) -> str:
    """Build the pinned panel message (title, help, marker)."""
    return "\n".join((strings["panel_title"], "", strings["panel_help"], DISTRIBUTOR_PANEL_MARKER))


def _distributor() -> Any | None:
    """Return the distributor service (None when unwired)."""
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

    return _wiring().distributor_service


async def _refresh_views_safe(guild: discord.Guild | None, kingdom_id: str) -> None:
    """Re-render the kingdom's salons after a mutation (best effort)."""
    try:
        from kingdoms.mods.kingdoms.kingdom_persistent import (
            _refresh_realm_views_for_kingdom_safe,
        )

        await _refresh_realm_views_for_kingdom_safe(guild, kingdom_id)
    except Exception:
        logger.info("KINGDOM DISTRIBUTOR: realm view refresh skipped", exc_info=True)


async def _error_note(exc: Exception, strings: dict[str, str]) -> str:
    """Build the standard failure line of the distributor flow."""
    return strings["failed"].format(getattr(exc, "message", None) or str(exc))


# ----------------------------------------------------------------------
# The persistent opening button
# ----------------------------------------------------------------------
class KingdomDistributorButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:distributor:open",
):
    """The restart-proof opening button of the pinned Distributeur panel."""

    def __init__(self, label: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                custom_id="kingdoms:distributor:open",
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
    ) -> KingdomDistributorButton:
        """Rebuild the button from the wire — THIS module's strings only."""
        return cls(_strings(_locale(interaction))["open_button"])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Guard the click, then open the kingdom picker (ephemeral)."""
        from kingdoms.mods.kingdoms.kingdom_persistent import _is_admin, _wiring

        strings = _strings(_locale(interaction))
        distributor = _distributor()
        if not _is_admin(interaction, _wiring().bot_admins):
            await interaction.response.send_message(strings["not_allowed"], ephemeral=True)
            return
        if distributor is None:
            await interaction.response.send_message(strings["no_service"], ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            kingdoms = await distributor.list_kingdoms()
        except Exception:
            kingdoms = []
        if not kingdoms:
            await interaction.followup.send(strings["no_kingdoms"], ephemeral=True)
            return
        view = _KingdomChooseView(_locale(interaction))
        await view.load_options(distributor)
        # fenetre-unique-globale-v1 (Drasah 30/10): the picker rides the
        # shared single-window registry (defer+followup → 3-tuple).
        from time import monotonic

        from kingdoms.mods.kingdoms.kingdom_persistent import (
            _EPHEMERAL_WINDOWS,
            _close_previous_ephemeral,
        )

        user_id = str(getattr(interaction.user, "id", ""))
        await _close_previous_ephemeral(user_id)
        window = await interaction.followup.send(
            strings["choose_kingdom"], view=view, ephemeral=True
        )
        _EPHEMERAL_WINDOWS[user_id] = (interaction, monotonic(), window)


class _KingdomChooseView(discord.ui.View):
    """The ephemeral kingdom picker — the flow's step 1."""

    def __init__(self, locale: str) -> None:
        super().__init__(timeout=600)
        self.locale = locale
        self.strings = _strings(locale)
        self.select: discord.ui.Select[Any] = discord.ui.Select(
            custom_id="kingdoms:distributor:kingdom",
            placeholder=self.strings["choose_kingdom"][:100],
            options=[discord.SelectOption(label="—", value="")],
            min_values=1,
            max_values=1,
        )
        self.select.callback = self._on_choose  # type: ignore[method-assign]
        self.add_item(self.select)

    async def load_options(self, distributor: Any) -> None:
        """Fill the select with the season's kingdoms (name + type)."""
        options: list[discord.SelectOption] = []
        for kingdom in await distributor.list_kingdoms():
            label = "👾 " + kingdom.name if kingdom.is_gaia else kingdom.name
            options.append(discord.SelectOption(label=label[:100], value=str(kingdom.id)))
        self.select.options = options[:25]

    async def _on_choose(self, interaction: discord.Interaction) -> None:
        kingdom_id = (self.select.values or [""])[0]
        if not kingdom_id:
            return
        view = _MenuView(self.locale, kingdom_id)
        await view.refresh()
        await interaction.response.edit_message(
            content=self.strings["menu_title"].format(kingdom=view.kingdom_name),
            view=view,
        )


# ----------------------------------------------------------------------
# Step 2 — the category menu (keeps the kingdom in memory)
# ----------------------------------------------------------------------
class _MenuView(discord.ui.View):
    """The ephemeral category menu of one kingdom."""

    def __init__(self, locale: str, kingdom_id: str) -> None:
        super().__init__(timeout=600)
        self.locale = locale
        self.strings = _strings(locale)
        self.kingdom_id = kingdom_id
        self.kingdom_name = kingdom_id

    async def refresh(self) -> None:
        """Re-read the kingdom's display name (fresh at every step)."""
        distributor = _distributor()
        if distributor is not None:
            try:
                kingdom = await distributor.kingdom(self.kingdom_id)
                self.kingdom_name = kingdom.name
            except Exception:
                logger.info("KINGDOM DISTRIBUTOR: kingdom read failed", exc_info=True)

    async def _open(self, interaction: discord.Interaction, view: discord.ui.View, content: str) -> None:
        await interaction.response.edit_message(content=content, view=view)

    @discord.ui.button(label="+", style=discord.ButtonStyle.primary)
    async def alliance_add(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        view = _CivView(self.locale, self.kingdom_id, mode="add", page=0)
        await view.refresh()
        await self._open(interaction, view, view.title_text())

    @discord.ui.button(label="-", style=discord.ButtonStyle.primary)
    async def alliance_remove(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        view = _CivView(self.locale, self.kingdom_id, mode="remove", page=0)
        await view.refresh()
        await self._open(interaction, view, view.title_text())

    @discord.ui.button(label="🔬", style=discord.ButtonStyle.secondary)
    async def treasury(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        view = _TreasuryView(self.locale, self.kingdom_id)
        await view.refresh()
        await self._open(interaction, view, view.title_text())

    @discord.ui.button(label="💒", style=discord.ButtonStyle.secondary)
    async def parish(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        view = _ParishView(self.locale, self.kingdom_id)
        await view.refresh()
        await self._open(interaction, view, view.title_text())

    @discord.ui.button(label="🗺️", style=discord.ButtonStyle.secondary)
    async def territories(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        view = _TerritoriesView(self.locale, self.kingdom_id)
        await view.refresh()
        await self._open(interaction, view, await view._content())

    @discord.ui.button(label="👤", style=discord.ButtonStyle.secondary)
    async def lord(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        view = _LordView(self.locale, self.kingdom_id)
        await view.refresh()
        await self._open(interaction, view, view.title_text())

    @discord.ui.button(label="⚔️", style=discord.ButtonStyle.secondary)
    async def tech(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        view = _TechView(self.locale, self.kingdom_id)
        await view.refresh()
        await self._open(interaction, view, view.title_text())

    @discord.ui.button(label="🧾", style=discord.ButtonStyle.secondary)
    async def status(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        content = await _status_text(self.locale, self.kingdom_id)
        view = _BackView(self.locale, self.kingdom_id)
        await self._open(interaction, view, content)

    @discord.ui.button(label="🔙", style=discord.ButtonStyle.danger)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        distributor = _distributor()
        if distributor is None:
            await interaction.response.edit_message(content=self.strings["no_service"], view=None)
            return
        choose = _KingdomChooseView(self.locale)
        await choose.load_options(distributor)
        await interaction.response.edit_message(
            content=self.strings["choose_kingdom"], view=choose
        )


class _BackView(discord.ui.View):
    """The one-button 'back to the category menu' footer."""

    def __init__(self, locale: str, kingdom_id: str) -> None:
        super().__init__(timeout=600)
        self.locale = locale
        self.strings = _strings(locale)
        self.kingdom_id = kingdom_id

    @discord.ui.button(label="🔙", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        view = _MenuView(self.locale, self.kingdom_id)
        await view.refresh()
        await interaction.response.edit_message(
            content=self.strings["menu_title"].format(kingdom=view.kingdom_name),
            view=view,
        )


# ----------------------------------------------------------------------
# Alliances — the add/remove civ selects (paginated, whole catalog)
# ----------------------------------------------------------------------
class _CivView(discord.ui.View):
    """The ephemeral civilization select (add or remove mode)."""

    def __init__(self, locale: str, kingdom_id: str, mode: str, page: int) -> None:
        super().__init__(timeout=600)
        self.locale = locale
        self.strings = _strings(locale)
        self.kingdom_id = kingdom_id
        self.mode = mode
        self.page = page
        self.kingdom_name = kingdom_id
        self.rows: list[dict[str, str]] = []
        self.pages = 1
        self.select: discord.ui.Select[Any] = discord.ui.Select(
            custom_id=f"kingdoms:distributor:civ:{mode}",
            options=[discord.SelectOption(label="—", value="")],
            min_values=1,
            max_values=1,
        )
        self.select.callback = self._on_choose  # type: ignore[method-assign]
        self.add_item(self.select)
        self.prev_button: discord.ui.Button[Any] = discord.ui.Button(
            label=self.strings["prev_page"], style=discord.ButtonStyle.secondary
        )
        self.prev_button.callback = self._on_prev  # type: ignore[method-assign]
        self.next_button: discord.ui.Button[Any] = discord.ui.Button(
            label=self.strings["next_page"], style=discord.ButtonStyle.secondary
        )
        self.next_button.callback = self._on_next  # type: ignore[method-assign]
        self.add_item(self.prev_button)
        self.add_item(self.next_button)
        back = _BackButton(self.strings["back_button"])
        back.callback = _back_to_menu(self.locale, kingdom_id)  # type: ignore[method-assign]
        self.add_item(back)

    async def refresh(self) -> None:
        """Re-read the kingdom, its secured civs and the catalog rows."""
        distributor = _distributor()
        if distributor is None:
            return
        kingdom = await distributor.kingdom(self.kingdom_id)
        self.kingdom_name = kingdom.name
        self._secured_keys = set(kingdom.secured_civilizations)
        all_rows = await distributor.civilization_options(self.kingdom_id)
        if self.mode == "remove":
            all_rows = [row for row in all_rows if row["status"] == "hand"]
        self.rows = all_rows
        self.pages = max(1, (len(all_rows) + PAGE_SIZE - 1) // PAGE_SIZE)
        self.page = min(self.page, self.pages - 1)
        start = self.page * PAGE_SIZE
        options: list[discord.SelectOption] = []
        for row in all_rows[start : start + PAGE_SIZE]:
            description = self._row_description(row)
            options.append(
                discord.SelectOption(
                    label=row["display_name"][:100],
                    description=description[:100],
                    value=row["key"],
                )
            )
        self.select.placeholder = self.title_text()[:100]
        self.select.options = options or [discord.SelectOption(label="—", value="")]
        self.prev_button.disabled = self.page <= 0
        self.next_button.disabled = self.page >= self.pages - 1

    def _row_description(self, row: dict[str, str]) -> str:
        if self.mode == "remove":
            if row["key"] in self._secured:
                return self.strings["civ_secured"]
            return "—"
        if row["status"] == "hand":
            return self.strings["civ_status_hand"]
        if row["status"] == "married":
            return self.strings["civ_status_married"].format(lord=row["detail"])
        return self.strings["civ_status_free"]

    @property
    def _secured(self) -> set[str]:
        return {row["key"] for row in self.rows if row["key"] in getattr(self, "_secured_keys", set())}

    def title_text(self) -> str:
        key = "civ_add_title" if self.mode == "add" else "civ_remove_title"
        return self.strings[key].format(
            kingdom=self.kingdom_name, page=self.page + 1, pages=self.pages
        )

    async def _on_prev(self, interaction: discord.Interaction) -> None:
        self.page = max(0, self.page - 1)
        await self.refresh()
        await interaction.response.edit_message(content=self.title_text(), view=self)

    async def _on_next(self, interaction: discord.Interaction) -> None:
        self.page = min(self.pages - 1, self.page + 1)
        await self.refresh()
        await interaction.response.edit_message(content=self.title_text(), view=self)

    async def _on_choose(self, interaction: discord.Interaction) -> None:
        civ = (self.select.values or [""])[0]
        if not civ:
            return
        distributor = _distributor()
        if distributor is None:
            await interaction.response.edit_message(content=self.strings["no_service"], view=None)
            return
        try:
            if self.mode == "add":
                await distributor.add_alliance(self.kingdom_id, civ)
                note = self.strings["civ_add_done"].format(
                    kingdom=self.kingdom_name, civ=civ
                )
            else:
                await distributor.remove_alliance(self.kingdom_id, civ)
                note = self.strings["civ_remove_done"].format(
                    kingdom=self.kingdom_name, civ=civ
                )
        except Exception as exc:
            await interaction.response.edit_message(
                content=await _error_note(exc, self.strings), view=self
            )
            return
        await _refresh_views_safe(interaction.guild, self.kingdom_id)
        await self.refresh()
        await interaction.response.edit_message(
            content="\n".join((note, self.title_text())), view=self
        )


def _back_to_menu(locale: str, kingdom_id: str) -> Any:
    """Build the shared 🔙 callback (menu title refreshed on arrival)."""

    async def _callback(interaction: discord.Interaction) -> None:
        strings = _strings(locale)
        view = _MenuView(locale, kingdom_id)
        await view.refresh()
        await interaction.response.edit_message(
            content=strings["menu_title"].format(kingdom=view.kingdom_name), view=view
        )

    return _callback


class _BackButton(discord.ui.Button[Any]):
    """A plain back button wired with the shared callback factory."""

    def __init__(self, label: str) -> None:
        super().__init__(label=label[:80], style=discord.ButtonStyle.danger)


# ----------------------------------------------------------------------
# Treasury & Parish — quick +/- buttons and a set-value modal
# ----------------------------------------------------------------------
class _TreasuryView(discord.ui.View):
    """The ephemeral tech-points wallet editor."""

    def __init__(self, locale: str, kingdom_id: str) -> None:
        super().__init__(timeout=600)
        self.locale = locale
        self.strings = _strings(locale)
        self.kingdom_id = kingdom_id
        self.kingdom_name = kingdom_id
        self.wallet = 0
        back = _BackButton(self.strings["back_button"])
        back.callback = _back_to_menu(locale, kingdom_id)  # type: ignore[method-assign]
        self.add_item(back)

    async def refresh(self) -> None:
        distributor = _distributor()
        if distributor is None:
            return
        kingdom = await distributor.kingdom(self.kingdom_id)
        self.kingdom_name = kingdom.name
        self.wallet = kingdom.tech_points_bank

    def title_text(self) -> str:
        return self.strings["treasury_title"].format(
            kingdom=self.kingdom_name, wallet=self.wallet
        )

    async def _apply(self, interaction: discord.Interaction, value: int, note: str) -> None:
        distributor = _distributor()
        if distributor is None:
            await interaction.response.edit_message(content=self.strings["no_service"], view=None)
            return
        try:
            await distributor.set_tech_points(self.kingdom_id, value)
        except Exception as exc:
            await interaction.response.edit_message(
                content=await _error_note(exc, self.strings), view=self
            )
            return
        await _refresh_views_safe(interaction.guild, self.kingdom_id)
        self.wallet = max(0, value)
        await interaction.response.edit_message(
            content="\n".join((note, self.title_text())), view=self
        )

    @discord.ui.button(label="+5", style=discord.ButtonStyle.primary)
    async def plus5(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        await self._apply(interaction, self.wallet + 5, "✅ +5 🔬")

    @discord.ui.button(label="+1", style=discord.ButtonStyle.primary)
    async def plus1(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        await self._apply(interaction, self.wallet + 1, "✅ +1 🔬")

    @discord.ui.button(label="-1", style=discord.ButtonStyle.secondary)
    async def minus1(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        await self._apply(interaction, self.wallet - 1, "- 1 🔬")

    @discord.ui.button(label="-5", style=discord.ButtonStyle.secondary)
    async def minus5(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        await self._apply(interaction, self.wallet - 5, "- 5 🔬")

    @discord.ui.button(label="📝", style=discord.ButtonStyle.secondary)
    async def set_value(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        await interaction.response.send_modal(
            _ValueModal(self.locale, self.kingdom_id, "tech_points_bank", self.wallet)
        )


class _ValueModal(discord.ui.Modal):
    """The ephemeral set-value form (treasury, points, tokens)."""

    value: discord.ui.TextInput[_ValueModal] = discord.ui.TextInput(
        label="0", max_length=8, required=True
    )

    def __init__(self, locale: str, kingdom_id: str, field: str, current: int) -> None:
        self.locale = locale
        self.strings = _strings(locale)
        self.kingdom_id = kingdom_id
        self.field = field
        self.value.label = self.strings["value_modal_label"][:45]
        self.value.placeholder = str(current)
        super().__init__(title=self.strings["value_modal_title"][:45], timeout=300)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        distributor = _distributor()
        raw = (self.value.value or "").strip()
        try:
            number = int(raw)
            if number < 0:
                raise ValueError(raw)
        except ValueError:
            await interaction.response.send_message(
                self.strings["value_modal_bad"], ephemeral=True
            )
            return
        if distributor is None:
            await interaction.response.send_message(self.strings["no_service"], ephemeral=True)
            return
        try:
            if self.field == "tech_points_bank":
                await distributor.set_tech_points(self.kingdom_id, number)
            elif self.field == "marriage_points":
                await distributor.set_marriage_points(self.kingdom_id, number)
            else:
                await distributor.set_arranged_tokens(self.kingdom_id, number)
        except Exception as exc:
            await interaction.response.send_message(
                await _error_note(exc, self.strings), ephemeral=True
            )
            return
        await _refresh_views_safe(interaction.guild, self.kingdom_id)
        await interaction.response.send_message(
            self.strings["value_set_done"].format(field=self.field, value=number),
            ephemeral=True,
        )


class _ParishView(discord.ui.View):
    """The ephemeral Paroisse editor (palier, points, tokens)."""

    def __init__(self, locale: str, kingdom_id: str) -> None:
        super().__init__(timeout=600)
        self.locale = locale
        self.strings = _strings(locale)
        self.kingdom_id = kingdom_id
        self.kingdom_name = kingdom_id
        self.kingdom: Any = None
        back = _BackButton(self.strings["back_button"])
        back.callback = _back_to_menu(locale, kingdom_id)  # type: ignore[method-assign]
        self.add_item(back)

    async def refresh(self) -> None:
        distributor = _distributor()
        if distributor is None:
            return
        self.kingdom = await distributor.kingdom(self.kingdom_id)
        self.kingdom_name = self.kingdom.name

    def title_text(self) -> str:
        tier = self.kingdom.parish_tier if self.kingdom else 1
        return self.strings["parish_title"].format(
            kingdom=self.kingdom_name,
            tier=f"palier {tier}",
            points=getattr(self.kingdom, "marriage_points", 0),
            tokens=getattr(self.kingdom, "arranged_marriage_stock", 0),
        )

    async def _set_kingdom_field(
        self, interaction: discord.Interaction, field: str, value: int
    ) -> None:
        distributor = _distributor()
        if distributor is None:
            await interaction.response.edit_message(content=self.strings["no_service"], view=None)
            return
        try:
            if field == "parish_tier":
                await distributor.set_parish_tier(self.kingdom_id, value)
            elif field == "marriage_points":
                await distributor.set_marriage_points(self.kingdom_id, value)
            else:
                await distributor.set_arranged_tokens(self.kingdom_id, value)
        except Exception as exc:
            await interaction.response.edit_message(
                content=await _error_note(exc, self.strings), view=self
            )
            return
        await _refresh_views_safe(interaction.guild, self.kingdom_id)
        await self.refresh()
        await interaction.response.edit_message(content=self.title_text(), view=self)

    @discord.ui.button(label="1", style=discord.ButtonStyle.secondary)
    async def tier1(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        await self._set_kingdom_field(interaction, "parish_tier", 1)

    @discord.ui.button(label="2", style=discord.ButtonStyle.secondary)
    async def tier2(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        await self._set_kingdom_field(interaction, "parish_tier", 2)

    @discord.ui.button(label="3", style=discord.ButtonStyle.secondary)
    async def tier3(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        await self._set_kingdom_field(interaction, "parish_tier", 3)

    @discord.ui.button(label="💍+1", style=discord.ButtonStyle.primary)
    async def point_plus(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        kingdom = await self._fresh_kingdom()
        await self._set_kingdom_field(
            interaction, "marriage_points", int(getattr(kingdom, "marriage_points", 0)) + 1
        )

    @discord.ui.button(label="💍-1", style=discord.ButtonStyle.secondary)
    async def point_minus(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        kingdom = await self._fresh_kingdom()
        await self._set_kingdom_field(
            interaction, "marriage_points", int(getattr(kingdom, "marriage_points", 0)) - 1
        )

    @discord.ui.button(label="🎫+1", style=discord.ButtonStyle.primary)
    async def token_plus(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        kingdom = await self._fresh_kingdom()
        await self._set_kingdom_field(
            interaction,
            "arranged_marriage_stock",
            int(getattr(kingdom, "arranged_marriage_stock", 0)) + 1,
        )

    @discord.ui.button(label="🎫-1", style=discord.ButtonStyle.secondary)
    async def token_minus(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        kingdom = await self._fresh_kingdom()
        await self._set_kingdom_field(
            interaction,
            "arranged_marriage_stock",
            int(getattr(kingdom, "arranged_marriage_stock", 0)) - 1,
        )

    @discord.ui.button(label="📝", style=discord.ButtonStyle.secondary)
    async def set_points(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        kingdom = await self._fresh_kingdom()
        await interaction.response.send_modal(
            _ValueModal(self.locale, self.kingdom_id, "marriage_points", int(getattr(kingdom, "marriage_points", 0)))
        )

    async def _fresh_kingdom(self) -> Any:
        distributor = _distributor()
        if distributor is None:
            raise RuntimeError("distributor unwired")
        return await distributor.kingdom(self.kingdom_id)


# ----------------------------------------------------------------------
# Territories — give / transfer / retire / replace / shield
# ----------------------------------------------------------------------
class _TerritoriesView(discord.ui.View):
    """The ephemeral territory actions of one kingdom."""

    def __init__(self, locale: str, kingdom_id: str) -> None:
        super().__init__(timeout=600)
        self.locale = locale
        self.strings = _strings(locale)
        self.kingdom_id = kingdom_id
        self.kingdom_name = kingdom_id
        back = _BackButton(self.strings["back_button"])
        back.callback = _back_to_menu(locale, kingdom_id)  # type: ignore[method-assign]
        self.add_item(back)

    async def refresh(self) -> None:
        distributor = _distributor()
        if distributor is None:
            return
        self.kingdom_name = (await distributor.kingdom(self.kingdom_id)).name

    async def _content(self) -> str:
        distributor = _distributor()
        if distributor is None:
            return self.strings["no_service"]
        territories = await distributor.territories_of(self.kingdom_id)
        lines = [
            self.strings["terr_title"].format(
                kingdom=self.kingdom_name, count=len(territories)
            )
        ]
        for territory in territories[:25]:
            shield = (
                self.strings["terr_shielded"] if territory.protected_until else ""
            )
            lines.append(
                self.strings["terr_line"].format(map=territory.map_key, shield=shield)
            )
        if not territories:
            lines.append(self.strings["terr_none"])
        return "\n".join(lines)

    async def _open_pick(
        self, interaction: discord.Interaction, action: str
    ) -> None:
        view = _TerritoryPickView(self.locale, self.kingdom_id, action)
        loaded = await view.refresh()
        if loaded is None:
            await interaction.response.edit_message(
                content=self.strings["terr_none"], view=self
            )
            return
        await interaction.response.edit_message(
            content=self.strings["terr_pick_line"].format(kingdom=self.kingdom_name),
            view=view,
        )

    @discord.ui.button(label="+", style=discord.ButtonStyle.primary)
    async def give(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        view = _MapGiveView(self.locale, self.kingdom_id)
        loaded = await view.refresh()
        if loaded is None:
            await interaction.response.edit_message(
                content=self.strings["terr_maps_none"], view=self
            )
            return
        await interaction.response.edit_message(
            content=self.strings["terr_give_pick_line"], view=view
        )

    @discord.ui.button(label="🔁", style=discord.ButtonStyle.primary)
    async def transfer(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        await self._open_pick(interaction, "transfer")

    @discord.ui.button(label="-", style=discord.ButtonStyle.danger)
    async def retire(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        await self._open_pick(interaction, "retire")

    @discord.ui.button(label="♻", style=discord.ButtonStyle.secondary)
    async def replace(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        await self._open_pick(interaction, "replace")

    @discord.ui.button(label="🛡", style=discord.ButtonStyle.secondary)
    async def shield(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        await self._open_pick(interaction, "shield")

    @discord.ui.button(label="🔓", style=discord.ButtonStyle.secondary)
    async def unshield(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        await self._open_pick(interaction, "unshield")


class _TerritoryPickView(discord.ui.View):
    """Pick one of the kingdom's territories, then apply the action."""

    def __init__(self, locale: str, kingdom_id: str, action: str) -> None:
        super().__init__(timeout=600)
        self.locale = locale
        self.strings = _strings(locale)
        self.kingdom_id = kingdom_id
        self.action = action
        self.select: discord.ui.Select[Any] = discord.ui.Select(
            custom_id=f"kingdoms:distributor:terr:{action}",
            options=[discord.SelectOption(label="—", value="")],
            min_values=1,
            max_values=1,
        )
        self.select.callback = self._on_choose  # type: ignore[method-assign]
        self.add_item(self.select)
        back = _BackButton(self.strings["back_button"])
        back.callback = _territories_back(locale, kingdom_id)  # type: ignore[method-assign]
        self.add_item(back)
        self.territories: dict[str, Any] = {}

    async def refresh(self) -> bool | None:
        """Fill the select; None when the kingdom owns no territory."""
        distributor = _distributor()
        if distributor is None:
            return None
        territories = await distributor.territories_of(self.kingdom_id)
        if not territories:
            return None
        self.territories = {str(territory.id): territory for territory in territories[:25]}
        self.select.options = [
            discord.SelectOption(
                label=territory.map_key[:100],
                value=str(territory.id),
                description=(
                    self.strings["terr_shielded"] if territory.protected_until else "—"
                )[:100],
            )
            for territory in self.territories.values()
        ]
        return True

    async def _on_choose(self, interaction: discord.Interaction) -> None:
        territory_id = (self.select.values or [""])[0]
        territory = self.territories.get(territory_id)
        if territory is None:
            return
        distributor = _distributor()
        if distributor is None:
            await interaction.response.edit_message(content=self.strings["no_service"], view=None)
            return
        if self.action == "transfer":
            view = _KingdomPickView(self.locale, self.kingdom_id, territory.id, territory.map_key)
            loaded = await view.refresh()
            if loaded is None:
                await interaction.response.edit_message(
                    content=self.strings["no_kingdoms"], view=None
                )
                return
            await interaction.response.edit_message(
                content=self.strings["terr_pick_kingdom_line"].format(map=territory.map_key),
                view=view,
            )
            return
        strings = self.strings
        try:
            if self.action == "retire":
                await distributor.retire_territory(territory.map_key)
                note = strings["terr_retire_done"].format(map=territory.map_key)
            elif self.action == "replace":
                await distributor.replace_territory(territory.map_key)
                note = strings["terr_replace_done"].format(map=territory.map_key)
            elif self.action == "shield":
                from kingdoms.mods.kingdoms.distributor import SHIELD_HOURS

                await distributor.shield_territory(territory.map_key, SHIELD_HOURS)
                note = strings["terr_shield_done"].format(map=territory.map_key)
            else:
                await distributor.shield_territory(territory.map_key, 0)
                note = strings["terr_unshield_done"].format(map=territory.map_key)
        except Exception as exc:
            await interaction.response.edit_message(
                content=await _error_note(exc, strings), view=self
            )
            return
        await _refresh_views_safe(interaction.guild, self.kingdom_id)
        back = _TerritoriesView(self.locale, self.kingdom_id)
        await back.refresh()
        await interaction.response.edit_message(
            content="\n".join((note, await back._content())), view=back
        )


def _territories_back(locale: str, kingdom_id: str) -> Any:
    """Return the shared callback returning to the territory actions view."""

    async def _callback(interaction: discord.Interaction) -> None:
        view = _TerritoriesView(locale, kingdom_id)
        await view.refresh()
        await interaction.response.edit_message(
            content=await view._content(), view=view
        )

    return _callback


class _MapGiveView(discord.ui.View):
    """Pick one still-available map and hand it to the kingdom."""

    def __init__(self, locale: str, kingdom_id: str) -> None:
        super().__init__(timeout=600)
        self.locale = locale
        self.strings = _strings(locale)
        self.kingdom_id = kingdom_id
        self.select: discord.ui.Select[Any] = discord.ui.Select(
            custom_id="kingdoms:distributor:map:give",
            options=[discord.SelectOption(label="—", value="")],
            min_values=1,
            max_values=1,
        )
        self.select.callback = self._on_choose  # type: ignore[method-assign]
        self.add_item(self.select)
        back = _BackButton(self.strings["back_button"])
        back.callback = _territories_back(locale, kingdom_id)  # type: ignore[method-assign]
        self.add_item(back)

    async def refresh(self) -> bool | None:
        distributor = _distributor()
        if distributor is None:
            return None
        maps = await distributor.available_maps()
        if not maps:
            return None
        self.select.options = [
            discord.SelectOption(label=display[:100], value=key)
            for key, display in maps[:25]
        ]
        return True

    async def _on_choose(self, interaction: discord.Interaction) -> None:
        map_key = (self.select.values or [""])[0]
        if not map_key:
            return
        distributor = _distributor()
        if distributor is None:
            await interaction.response.edit_message(content=self.strings["no_service"], view=None)
            return
        kingdom = await distributor.kingdom(self.kingdom_id)
        try:
            await distributor.give_map(self.kingdom_id, map_key)
        except Exception as exc:
            await interaction.response.edit_message(
                content=await _error_note(exc, self.strings), view=self
            )
            return
        await _refresh_views_safe(interaction.guild, self.kingdom_id)
        note = self.strings["terr_give_done"].format(
            map=map_key, kingdom=kingdom.name
        )
        back = _TerritoriesView(self.locale, self.kingdom_id)
        await back.refresh()
        await interaction.response.edit_message(
            content="\n".join((note, await back._content())), view=back
        )


class _KingdomPickView(discord.ui.View):
    """Pick the transfer target kingdom."""

    def __init__(self, locale: str, from_kingdom_id: str, territory_id: str, map_key: str) -> None:
        super().__init__(timeout=600)
        self.locale = locale
        self.strings = _strings(locale)
        self.from_kingdom_id = from_kingdom_id
        self.territory_id = territory_id
        self.map_key = map_key
        self.select: discord.ui.Select[Any] = discord.ui.Select(
            custom_id="kingdoms:distributor:terr:target",
            options=[discord.SelectOption(label="—", value="")],
            min_values=1,
            max_values=1,
        )
        self.select.callback = self._on_choose  # type: ignore[method-assign]
        self.add_item(self.select)
        back = _BackButton(self.strings["back_button"])
        back.callback = _territories_back(locale, from_kingdom_id)  # type: ignore[method-assign]
        self.add_item(back)

    async def refresh(self) -> bool | None:
        distributor = _distributor()
        if distributor is None:
            return None
        kingdoms = await distributor.list_kingdoms()
        options = [
            discord.SelectOption(
                label=("👾 " + kingdom.name if kingdom.is_gaia else kingdom.name)[:100],
                value=str(kingdom.id),
            )
            for kingdom in kingdoms
            if str(kingdom.id) != str(self.from_kingdom_id)
        ]
        if not options:
            return None
        self.select.options = options[:25]
        return True

    async def _on_choose(self, interaction: discord.Interaction) -> None:
        target = (self.select.values or [""])[0]
        if not target:
            return
        distributor = _distributor()
        if distributor is None:
            await interaction.response.edit_message(content=self.strings["no_service"], view=None)
            return
        try:
            await distributor.transfer_territory(self.territory_id, target)
            kingdom = await distributor.kingdom(target)
        except Exception as exc:
            await interaction.response.edit_message(
                content=await _error_note(exc, self.strings), view=self
            )
            return
        await _refresh_views_safe(interaction.guild, self.from_kingdom_id)
        await _refresh_views_safe(interaction.guild, target)
        note = self.strings["terr_transfer_done"].format(
            map=self.map_key, kingdom=kingdom.name
        )
        back = _TerritoriesView(self.locale, self.from_kingdom_id)
        await back.refresh()
        await interaction.response.edit_message(
            content="\n".join((note, await back._content())), view=back
        )


# ----------------------------------------------------------------------
# Lords — budgets and the marriage lock
# ----------------------------------------------------------------------
class _LordView(discord.ui.View):
    """Pick one active lord of the kingdom."""

    def __init__(self, locale: str, kingdom_id: str) -> None:
        super().__init__(timeout=600)
        self.locale = locale
        self.strings = _strings(locale)
        self.kingdom_id = kingdom_id
        self.kingdom_name = kingdom_id
        self.select: discord.ui.Select[Any] = discord.ui.Select(
            custom_id="kingdoms:distributor:lord",
            options=[discord.SelectOption(label="—", value="")],
            min_values=1,
            max_values=1,
        )
        self.select.callback = self._on_choose  # type: ignore[method-assign]
        self.add_item(self.select)
        back = _BackButton(self.strings["back_button"])
        back.callback = _back_to_menu(locale, kingdom_id)  # type: ignore[method-assign]
        self.add_item(back)

    async def refresh(self) -> None:
        distributor = _distributor()
        if distributor is None:
            return
        self.kingdom_name = (await distributor.kingdom(self.kingdom_id)).name
        overview = await distributor.overview(self.kingdom_id)
        lords = list(overview["lords"])
        if not lords:
            return
        options = []
        for lord in lords[:25]:
            role = (
                self.strings["lord_role_king"]
                if str(getattr(lord, "role", "")) == "king"
                else self.strings["lord_role_lord"]
            )
            options.append(
                discord.SelectOption(
                    label=f"{role} {lord.display_name}"[:100],
                    value=str(lord.id),
                    description=self.strings["lord_line"].format(
                        role=role, attack=lord.attack_used, defense=lord.defense_used
                    )[:100],
                )
            )
        self.select.options = options

    def title_text(self) -> str:
        return self.strings["lord_title"].format(kingdom=self.kingdom_name)

    async def _on_choose(self, interaction: discord.Interaction) -> None:
        lord_id = (self.select.values or [""])[0]
        if not lord_id:
            return
        view = _LordActionView(self.locale, self.kingdom_id, lord_id)
        await view.refresh()
        await interaction.response.edit_message(
            content=view.title_text(), view=view
        )


class _LordActionView(discord.ui.View):
    """The budget/lock actions of one lord."""

    def __init__(self, locale: str, kingdom_id: str, lord_id: str) -> None:
        super().__init__(timeout=600)
        self.locale = locale
        self.strings = _strings(locale)
        self.kingdom_id = kingdom_id
        self.lord_id = lord_id
        self.lord_name = lord_id
        back = _BackButton(self.strings["back_button"])
        back.callback = _lords_back(locale, kingdom_id)  # type: ignore[method-assign]
        self.add_item(back)

    async def refresh(self) -> None:
        distributor = _distributor()
        if distributor is None:
            return
        self.lord_name = (await distributor.lord(self.lord_id)).display_name

    def title_text(self) -> str:
        return self.strings["lord_action_title"].format(
            lord=self.lord_name, kingdom=self.kingdom_id
        )

    async def _apply(
        self, interaction: discord.Interaction, note: str, **call: Any
    ) -> None:
        distributor = _distributor()
        if distributor is None:
            await interaction.response.edit_message(content=self.strings["no_service"], view=None)
            return
        try:
            for method, kwargs in call.items():
                await getattr(distributor, method)(**kwargs)
        except Exception as exc:
            await interaction.response.edit_message(
                content=await _error_note(exc, self.strings), view=self
            )
            return
        await _refresh_views_safe(interaction.guild, self.kingdom_id)
        await interaction.response.edit_message(
            content="\n".join((note, self.title_text())), view=self
        )

    @discord.ui.button(label="↻", style=discord.ButtonStyle.primary)
    async def budgets(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        await self._apply(
            interaction,
            self.strings["lord_budgets_done"].format(lord=self.lord_name),
            reset_budgets={"lord_id": self.lord_id},
        )

    @discord.ui.button(label="🔓", style=discord.ButtonStyle.secondary)
    async def unlock(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        await self._apply(
            interaction,
            self.strings["lord_unlock_done"].format(lord=self.lord_name),
            set_marriage_lock={"lord_id": self.lord_id, "hours": None},
        )

    @discord.ui.button(label="🔒", style=discord.ButtonStyle.secondary)
    async def lock(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        from kingdoms.mods.kingdoms.distributor import LOCK_HOURS

        await self._apply(
            interaction,
            self.strings["lord_lock_done"].format(lord=self.lord_name),
            set_marriage_lock={"lord_id": self.lord_id, "hours": LOCK_HOURS},
        )


def _lords_back(locale: str, kingdom_id: str) -> Any:
    """Return the shared callback returning to the lord picker."""

    async def _callback(interaction: discord.Interaction) -> None:
        view = _LordView(locale, kingdom_id)
        await view.refresh()
        await interaction.response.edit_message(content=view.title_text(), view=view)

    return _callback


# ----------------------------------------------------------------------
# Combat technologies — forced purchase counters
# ----------------------------------------------------------------------
class _TechView(discord.ui.View):
    """Pick one combat technology of the kingdom."""

    def __init__(self, locale: str, kingdom_id: str) -> None:
        super().__init__(timeout=600)
        self.locale = locale
        self.strings = _strings(locale)
        self.kingdom_id = kingdom_id
        self.kingdom_name = kingdom_id
        self.select: discord.ui.Select[Any] = discord.ui.Select(
            custom_id="kingdoms:distributor:tech",
            options=[discord.SelectOption(label="—", value="")],
            min_values=1,
            max_values=1,
        )
        self.select.callback = self._on_choose  # type: ignore[method-assign]
        self.add_item(self.select)
        back = _BackButton(self.strings["back_button"])
        back.callback = _back_to_menu(locale, kingdom_id)  # type: ignore[method-assign]
        self.add_item(back)

    async def refresh(self) -> None:
        distributor = _distributor()
        if distributor is None:
            return
        self.kingdom_name = (await distributor.kingdom(self.kingdom_id)).name
        overview = await distributor.overview(self.kingdom_id)
        purchases: dict[str, int] = dict(overview["purchases"])
        from kingdoms.mods.kingdoms.attacks import COMBAT_TECHNOLOGIES

        self.select.options = [
            discord.SelectOption(
                label=tech,
                value=tech,
                description=self.strings["tech_line"].format(
                    count=purchases.get(tech, 0)
                )[:100],
            )
            for tech in COMBAT_TECHNOLOGIES
        ]

    def title_text(self) -> str:
        return self.strings["tech_title"].format(kingdom=self.kingdom_name)

    async def _on_choose(self, interaction: discord.Interaction) -> None:
        tech = (self.select.values or [""])[0]
        if not tech:
            return
        view = _TechActionView(self.locale, self.kingdom_id, tech)
        await view.refresh()
        await interaction.response.edit_message(content=view.title_text(), view=view)


class _TechActionView(discord.ui.View):
    """The +1/-1 counters of one combat technology."""

    def __init__(self, locale: str, kingdom_id: str, tech: str) -> None:
        super().__init__(timeout=600)
        self.locale = locale
        self.strings = _strings(locale)
        self.kingdom_id = kingdom_id
        self.tech = tech
        self.count = 0
        back = _BackButton(self.strings["back_button"])
        back.callback = _tech_back(locale, kingdom_id)  # type: ignore[method-assign]
        self.add_item(back)

    async def refresh(self) -> None:
        distributor = _distributor()
        if distributor is None:
            return
        overview = await distributor.overview(self.kingdom_id)
        purchases: dict[str, int] = dict(overview["purchases"])
        self.count = int(purchases.get(self.tech, 0))

    def title_text(self) -> str:
        return self.strings["tech_action_title"].format(tech=self.tech, count=self.count)

    async def _apply(self, interaction: discord.Interaction, delta: int) -> None:
        distributor = _distributor()
        if distributor is None:
            await interaction.response.edit_message(content=self.strings["no_service"], view=None)
            return
        try:
            self.count = await distributor.set_technology_count(
                self.kingdom_id, self.tech, max(0, self.count + delta)
            )
        except Exception as exc:
            await interaction.response.edit_message(
                content=await _error_note(exc, self.strings), view=self
            )
            return
        await _refresh_views_safe(interaction.guild, self.kingdom_id)
        await interaction.response.edit_message(content=self.title_text(), view=self)

    @discord.ui.button(label="✅", style=discord.ButtonStyle.primary)
    async def plus(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        await self._apply(interaction, +1)

    @discord.ui.button(label="❌", style=discord.ButtonStyle.secondary)
    async def minus(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        await self._apply(interaction, -1)


def _tech_back(locale: str, kingdom_id: str) -> Any:
    """Return the shared callback returning to the technology picker."""

    async def _callback(interaction: discord.Interaction) -> None:
        view = _TechView(locale, kingdom_id)
        await view.refresh()
        await interaction.response.edit_message(content=view.title_text(), view=view)

    return _callback


# ----------------------------------------------------------------------
# The 🧾 status block
# ----------------------------------------------------------------------
async def _status_text(locale: str, kingdom_id: str) -> str:
    """Render the compact overview of a kingdom (🧾 Statut view)."""
    strings = _strings(locale)
    distributor = _distributor()
    if distributor is None:
        return strings["no_service"]
    try:
        overview = await distributor.overview(kingdom_id)
    except Exception as exc:
        return await _error_note(exc, strings)
    kingdom = overview["kingdom"]
    tier = overview["tier"]
    lines = [
        strings["status_title"].format(
            kingdom=kingdom.name, validation=str(getattr(kingdom, "validation", ""))
        ),
        strings["status_parish"].format(
            tier=tier.display_name,
            tech=kingdom.tech_points_bank,
            points=kingdom.marriage_points,
            tokens=kingdom.arranged_marriage_stock,
        ),
    ]
    hand = list(kingdom.civilizations)
    lines.append(
        strings["status_hand"].format(count=len(hand), civs=", ".join(hand) or "—")
    )
    secured = list(kingdom.secured_civilizations)
    if secured:
        lines.append(strings["status_secured"].format(civs=", ".join(secured)))
    from datetime import UTC, datetime

    for lord in overview["lords"]:
        role = (
            strings["lord_role_king"]
            if str(getattr(lord, "role", "")) == "king"
            else strings["lord_role_lord"]
        )
        marriage = lord.married_civilization or strings["status_married_none"]
        lock = ""
        if lord.marriage_locked_until:
            stamp = datetime.fromtimestamp(lord.marriage_locked_until, tz=UTC)
            lock = strings["status_lock_line"].format(until=f"{stamp:%d/%m %Hh%M}")
        lines.append(
            strings["status_lord"].format(
                role=role,
                name=lord.display_name,
                marriage=marriage,
                attack=lord.attack_used,
                defense=lord.defense_used,
                lock=lock,
            )
        )
    territories = list(overview["territories"])
    lines.append(
        strings["status_territories"].format(
            count=len(territories),
            maps=", ".join(territory.map_key for territory in territories) or "—",
        )
    )
    purchases: dict[str, int] = dict(overview["purchases"])
    lines.append(
        strings["status_purchases"].format(
            purchases=", ".join(f"{k}x{v}" for k, v in purchases.items()) or ""
        )
        if purchases
        else strings["status_purchases_none"]
    )
    return "\n".join(lines)


# ----------------------------------------------------------------------
# The salon deployment (pinned panel message)
# ----------------------------------------------------------------------
async def deploy_distributor_panel(channel: discord.TextChannel, locale: str) -> None:
    """Pin (or refresh) the distributor panel message in its salon."""
    strings = _strings(locale)
    for message in await _channel_messages(channel):
        if DISTRIBUTOR_PANEL_MARKER in (message.content or ""):
            try:
                await message.delete()
            except Exception:
                logger.warning("KINGDOM DISTRIBUTOR: old panel removal failed", exc_info=True)
    try:
        view = discord.ui.View(timeout=None)
        view.add_item(KingdomDistributorButton(strings["open_button"]))
        await channel.send(content=panel_text(strings), view=view)
    except Exception:
        logger.warning("KINGDOM DISTRIBUTOR: panel send failed", exc_info=True)


async def _channel_messages(channel: discord.TextChannel) -> list[discord.Message]:
    try:
        return [message async for message in channel.history(limit=50)]
    except Exception:
        logger.warning("KINGDOM DISTRIBUTOR: channel history read failed", exc_info=True)
        return []
