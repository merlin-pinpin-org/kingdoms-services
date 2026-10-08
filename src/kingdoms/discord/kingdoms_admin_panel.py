"""The Kingdoms mod's admin section: the Royaume panel (kingdoms#214 tranche ②).

The pinned bot-admins panel is the single admin surface
(CONVENTIONS.md); this module registers the Kingdoms mod's section
there through the ``admin_panel_mods`` seam — present when the mod is
enabled, absent when it is not, with no core panel code change.

This slice (tranche ②-a) surfaces the **season** policy operations of
the D75 socle: the per-kingdom recruitment switch, the global
applications switch, the kingdom/lord quotas and the foundation
rights. Roster and entity operations follow in the next slices.

Every action opens a reason modal first — D75 makes the reason
mandatory without exception, and the service layer re-checks it, so
the wire can never bypass the gate. Namespace discipline: one
custom_id, one dispatch path — ``admin:pin:mod:kingdoms:<action>``
rides the seam's own namespace, restart-proof, no captured state.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import discord

from kingdoms.core.exceptions import KingdomsError

logger = logging.getLogger("kingdoms.kingdoms_admin_panel")

__all__ = [
    "AdminReasonModal",
    "KingdomsAdminActionButton",
    "KingdomsAdminRecruitmentSelect",
    "register_kingdoms_admin_section",
    "section_entry",
    "unregister_kingdoms_admin_section",
]

_ACTION_PREFIX = "admin:pin:mod:kingdoms"

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "section_label": "🏰 Kingdoms",
        "section_description": "Season policy: recruitment, applications, quotas, foundation",
        "no_season": "No season service is wired — the panel is inactive on this run.",
        "title": "Kingdoms — season management",
        "subtitle": "D75: every action requires a reason, journaled with a 2h rollback window.",
        "recruitment_label": "**Recruitment switch** — pick a kingdom to flip it",
        "recruitment_placeholder": "Pick a kingdom to flip its recruitment",
        "recruitment_open": "open",
        "recruitment_closed": "closed",
        "applications_button": "Applications: {state}",
        "applications_on": "open",
        "applications_off": "closed",
        "quotas_button": "Kingdom / lord quotas",
        "foundation_button": "Foundation rights",
        "reason_label": "Reason (required — D75)",
        "reason_placeholder": "Why this admin action?",
        "quotas_kingdoms_label": "Kingdoms count (empty = config default)",
        "quotas_lords_label": "Lords per kingdom (empty = config default)",
        "foundation_king_label": "King founding (1/0, empty = unchanged)",
        "foundation_admin_label": "Admin founding (1/0, empty = unchanged)",
        "bad_number": "The quotas must be whole numbers (or empty to reset).",
        "bad_checkbox": "The foundation checkboxes take 1, 0 or nothing.",
        "done": "Done — the change is journaled (2h rollback window).",
        "failed": "The action failed: {error}",
    },
    "fr": {
        "section_label": "🏰 Royaume",
        "section_description": "Politique de saison : recrutement, candidatures, quotas, fondation",
        "no_season": "Aucun service de saison câblé — le panneau est inactif sur cette instance.",
        "title": "Royaume — gestion de saison",
        "subtitle": "D75 : toute action exige un motif, journalisée avec fenêtre de rollback de 2 h.",
        "recruitment_label": "**Interrupteur de recrutement** — choisir un royaume pour l'inverser",
        "recruitment_placeholder": "Choisir un royaume pour inverser son recrutement",
        "recruitment_open": "ouvert",
        "recruitment_closed": "fermé",
        "applications_button": "Candidatures : {state}",
        "applications_on": "ouvertes",
        "applications_off": "fermées",
        "quotas_button": "Quotas royaumes / seigneurs",
        "foundation_button": "Droits de fondation",
        "reason_label": "Motif (obligatoire — D75)",
        "reason_placeholder": "Pourquoi cette action admin ?",
        "quotas_kingdoms_label": "Nombre de royaumes (vide = défaut config)",
        "quotas_lords_label": "Seigneurs par royaume (vide = défaut config)",
        "foundation_king_label": "Fondation par Roi (1/0, vide = inchangé)",
        "foundation_admin_label": "Fondation par admin (1/0, vide = inchangé)",
        "bad_number": "Les quotas doivent être des entiers (ou vides pour réinitialiser).",
        "bad_checkbox": "Les cases de fondation prennent 1, 0 ou rien.",
        "done": "Fait — le changement est journalisé (fenêtre de rollback de 2 h).",
        "failed": "L'action a échoué : {error}",
    },
}


def _strings(locale: str) -> dict[str, str]:
    return STRINGS["fr"] if str(locale).lower().startswith("fr") else STRINGS["en"]


def _locale(interaction: discord.Interaction) -> str:
    return str(interaction.locale) if interaction.locale else "en"


def _kingdoms_service(interaction: discord.Interaction) -> Any | None:
    """Resolve the domain service from the live bot at click time."""
    return getattr(interaction.client, "kingdoms_service", None)


def _admin_service(interaction: discord.Interaction) -> Any | None:
    """Resolve the journaled admin service from the live bot at click time."""
    kingdoms_service = _kingdoms_service(interaction)
    if kingdoms_service is None:
        return None
    from kingdoms.mods.kingdoms.admin_service import KingdomAdminService

    return KingdomAdminService(kingdoms_service, kingdoms_service._store)


async def _answer(interaction: discord.Interaction, text: str) -> None:
    """Answer ephemerally, whatever the response state."""
    if not interaction.response.is_done():
        await interaction.response.send_message(text, ephemeral=True)
    else:
        await interaction.followup.send(text, ephemeral=True)


async def _season_kingdoms(interaction: discord.Interaction) -> tuple[Any | None, list[Any]]:
    """Read the current season and its player kingdoms (None season = unwired)."""
    kingdoms_service = _kingdoms_service(interaction)
    if kingdoms_service is None:
        return None, []
    try:
        kingdoms = [k for k in await kingdoms_service.kingdoms() if not k.is_gaia]
        return await kingdoms_service.current_season(), kingdoms
    except Exception:
        logger.warning("ROYAUME PANEL: season read failed", exc_info=True)
        return None, []


async def _run_season_action(
    interaction: discord.Interaction,
    operation: str,
    payload: dict[str, Any],
    reason: str,
) -> None:
    """Run one season operation through the D75 socle and answer."""
    strings = _strings(_locale(interaction))
    admin = _admin_service(interaction)
    if admin is None:
        await _answer(interaction, strings["no_season"])
        return
    actor = {
        "actor_id": str(interaction.user.id),
        "actor_name": interaction.user.display_name,
    }
    try:
        if operation == "recruitment":
            await admin.set_recruitment(payload["kingdom"], open=payload["open"], reason=reason, **actor)
        elif operation == "applications":
            await admin.set_applications(open=payload["open"], reason=reason, **actor)
        elif operation == "quotas":
            await admin.set_quotas(
                kingdoms_count=payload["kingdoms_count"],
                lords_per_kingdom=payload["lords_per_kingdom"],
                reason=reason,
                **actor,
            )
        elif operation == "foundation":
            await admin.set_foundation_rights(
                king=payload.get("king"),
                admin=payload.get("admin"),
                reason=reason,
                **actor,
            )
        else:  # pragma: no cover - the wire template only lets the four through
            await _answer(interaction, strings["failed"].format(error=operation))
            return
    except KingdomsError as error:
        await _answer(interaction, strings["failed"].format(error=error.message))
        return
    await _answer(interaction, strings["done"])


class AdminReasonModal(discord.ui.Modal):
    """The D75 reason gate of every Royaume panel action.

    The modal collects the mandatory reason plus the per-operation
    fields this slice needs; the service re-checks the reason, so an
    empty submission can never reach the store.
    """

    def __init__(self, interaction: discord.Interaction, operation: str, payload: dict[str, Any]) -> None:
        strings = _strings(_locale(interaction))
        super().__init__(title=strings["title"][:45], timeout=300)
        self._operation = operation
        self._payload = payload
        self._fields: dict[str, discord.ui.TextInput[AdminReasonModal]] = {}
        reason = discord.ui.TextInput(
            label=strings["reason_label"][:45],
            placeholder=strings["reason_placeholder"][:100],
            max_length=200,
            min_length=1,
            required=True,
        )
        self.add_item(reason)
        self._fields["reason"] = reason
        if operation == "quotas":
            for key, label_key in (
                ("kingdoms_count", "quotas_kingdoms_label"),
                ("lords_per_kingdom", "quotas_lords_label"),
            ):
                field = discord.ui.TextInput(label=strings[label_key][:45], required=False, max_length=4)
                self.add_item(field)
                self._fields[key] = field
        elif operation == "foundation":
            for key, label_key in (("king", "foundation_king_label"), ("admin", "foundation_admin_label")):
                field = discord.ui.TextInput(label=strings[label_key][:45], required=False, max_length=1)
                self.add_item(field)
                self._fields[key] = field

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Gate the reason, parse the extras, run the operation."""
        strings = _strings(_locale(interaction))
        reason = str(self._fields["reason"].value or "").strip()
        if not reason:
            await _answer(interaction, strings["reason_label"])
            return
        payload = dict(self._payload)
        for key in ("kingdoms_count", "lords_per_kingdom", "king", "admin"):
            if key not in self._fields:
                continue
            raw = str(self._fields[key].value or "").strip()
            if key in {"kingdoms_count", "lords_per_kingdom"}:
                if not raw:
                    payload[key] = None
                elif raw.isdigit():
                    payload[key] = int(raw)
                else:
                    await _answer(interaction, strings["bad_number"])
                    return
            else:
                if not raw:
                    payload[key] = None
                elif raw in {"0", "1"}:
                    payload[key] = raw == "1"
                else:
                    await _answer(interaction, strings["bad_checkbox"])
                    return
        await _run_season_action(interaction, self._operation, payload, reason)


def _recruitment_options(strings: dict[str, str], kingdoms: list[Any]) -> list[discord.SelectOption]:
    """Build the recruitment select options from the live kingdom list."""
    return [
        discord.SelectOption(
            label=kingdom.name[:100],
            description=strings["recruitment_open" if kingdom.recruitment_open else "recruitment_closed"][:100],
        )
        for kingdom in sorted(kingdoms, key=lambda k: k.name)
    ]


class KingdomsAdminRecruitmentSelect(
    discord.ui.DynamicItem[discord.ui.Select[Any]],
    template=rf"{_ACTION_PREFIX}:recruitment",
):
    """The per-kingdom recruitment switch, restart-proof through the wire."""

    def __init__(self, options: list[discord.SelectOption], placeholder: str) -> None:
        super().__init__(
            discord.ui.Select(custom_id=f"{_ACTION_PREFIX}:recruitment", options=options, placeholder=placeholder)
        )

    @classmethod
    def create(cls, interaction: discord.Interaction) -> KingdomsAdminRecruitmentSelect:
        """Rebuild the select from the live season (options at click time)."""
        strings = _strings(_locale(interaction))
        return cls([], strings["recruitment_placeholder"])

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomsAdminRecruitmentSelect:
        """Rebuild the select from the wire."""
        return cls.create(interaction)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Flip the chosen kingdom's recruitment through the reason modal."""
        strings = _strings(_locale(interaction))
        chosen = (self.item.values or [""])[0]
        _, kingdoms = await _season_kingdoms(interaction)
        kingdom = next((k for k in kingdoms if k.name == chosen), None)
        if kingdom is None:
            await _answer(interaction, strings["no_season"])
            return
        await interaction.response.send_modal(
            AdminReasonModal(
                interaction, "recruitment", {"kingdom": kingdom.name, "open": not kingdom.recruitment_open}
            )
        )


class KingdomsAdminActionButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_ACTION_PREFIX}:(?P<action>applications-open|applications-close|quotas|foundation)",
):
    """The season policy buttons: applications, quotas, foundation."""

    def __init__(self, action: str, label: str, style: discord.ButtonStyle) -> None:
        super().__init__(discord.ui.Button(label=label, custom_id=f"{_ACTION_PREFIX}:{action}", style=style))
        self.action = action

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomsAdminActionButton:
        """Rebuild the button from the wire."""
        strings = _strings(_locale(interaction))
        action = match.group("action")
        labels = {
            "applications-open": strings["applications_button"].format(state=strings["applications_on"]),
            "applications-close": strings["applications_button"].format(state=strings["applications_off"]),
            "quotas": strings["quotas_button"],
            "foundation": strings["foundation_button"],
        }
        styles = {
            "applications-open": discord.ButtonStyle.success,
            "applications-close": discord.ButtonStyle.secondary,
            "quotas": discord.ButtonStyle.primary,
            "foundation": discord.ButtonStyle.primary,
        }
        return cls(action, labels[action], styles[action])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the reason modal for the pressed season action."""
        if self.action == "applications-open":
            operation, payload = "applications", {"open": True}
        elif self.action == "applications-close":
            operation, payload = "applications", {"open": False}
        else:
            operation, payload = self.action, {}
        await interaction.response.send_modal(AdminReasonModal(interaction, operation, payload))


def _select_row(item: discord.ui.DynamicItem[Any]) -> discord.ui.ActionRow[discord.ui.LayoutView]:
    """Wrap one dynamic item in its own ActionRow (Discord layout rule)."""
    row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    row.add_item(item)
    return row


async def section_entry(interaction: discord.Interaction) -> discord.ui.LayoutView:
    """Build the Royaume section view — the seam's entry callback."""
    strings = _strings(_locale(interaction))
    view = discord.ui.LayoutView(timeout=None)
    blocks: list[Any] = [
        discord.ui.TextDisplay(f"# {strings['title']}"),
        discord.ui.TextDisplay(f"-# {strings['subtitle']}"),
    ]
    season, kingdoms = await _season_kingdoms(interaction)
    if season is None:
        blocks.append(discord.ui.Separator())
        blocks.append(discord.ui.TextDisplay(strings["no_season"]))
        view.add_item(discord.ui.Container(*blocks, accent_colour=None))
        return view
    blocks.append(discord.ui.Separator())
    if kingdoms:
        blocks.append(discord.ui.TextDisplay(strings["recruitment_label"]))
        options = _recruitment_options(strings, kingdoms)
        select = KingdomsAdminRecruitmentSelect(options, strings["recruitment_placeholder"])
        blocks.append(_select_row(select))
    applications_open = bool(getattr(season, "applications_open", False))
    applications_action = "applications-close" if applications_open else "applications-open"
    row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    row.add_item(
        KingdomsAdminActionButton(
            applications_action,
            strings["applications_button"].format(
                state=strings["applications_on" if applications_open else "applications_off"]
            ),
            discord.ButtonStyle.success if applications_open else discord.ButtonStyle.secondary,
        )
    )
    row.add_item(KingdomsAdminActionButton("quotas", strings["quotas_button"], discord.ButtonStyle.primary))
    row.add_item(KingdomsAdminActionButton("foundation", strings["foundation_button"], discord.ButtonStyle.primary))
    blocks.append(row)
    view.add_item(discord.ui.Container(*blocks, accent_colour=None))
    return view


def register_kingdoms_admin_section(bot: discord.Client) -> None:
    """Register the Royaume section and its persistent components on the bot."""
    from kingdoms.discord.admin_panel_mods import AdminModSection, register_admin_mod_section

    register_admin_mod_section(
        AdminModSection(
            mod="kingdoms",
            label=STRINGS["fr"]["section_label"],
            entry=section_entry,
            description=STRINGS["fr"]["section_description"],
        )
    )
    bot.add_dynamic_items(KingdomsAdminRecruitmentSelect, KingdomsAdminActionButton)


def unregister_kingdoms_admin_section() -> None:
    """Drop the Royaume section (mod disabled or tests)."""
    from kingdoms.discord.admin_panel_mods import unregister_admin_mod_section

    unregister_admin_mod_section("kingdoms")
