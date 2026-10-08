"""The Kingdoms mod's admin section: the Royaume panel (kingdoms#214 tranche ②).

The pinned bot-admins panel is the single admin surface
(CONVENTIONS.md); this module registers the Kingdoms mod's section
there through the ``admin_panel_mods`` seam — present when the mod is
enabled, absent when it is not, with no core panel code change.

This slice (tranche ②-b) adds the **roster** operations of the D75
socle: the manual add, the queued assignment, the reassignment, the
eject back to the queue and the throne swap. Entity operations and
the journal view follow in the next slice.

Every action opens a reason modal first — D75 makes the reason
mandatory without exception, and the service layer re-checks it, so
the wire can never bypass the gate. Namespace discipline: one
custom_id, one dispatch path — ``admin:pin:mod:kingdoms:<action>``
rides the seam's own namespace, restart-proof, no captured state.
"""

from __future__ import annotations

import logging
import re
from typing import Any, ClassVar

import discord

from kingdoms.core.exceptions import KingdomsError

logger = logging.getLogger("kingdoms.kingdoms_admin_panel")

__all__ = [
    "AdminReasonModal",
    "KingdomsAdminActionButton",
    "KingdomsAdminRecruitmentSelect",
    "KingdomsAdminRosterButton",
    "register_kingdoms_admin_section",
    "section_entry",
    "unregister_kingdoms_admin_section",
]

_ACTION_PREFIX = "admin:pin:mod:kingdoms"

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "section_label": "🏰 Kingdoms",
        "section_description": "Season and roster: recruitment, applications, quotas, foundation, lords, throne",
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
        "roster_label": "**Roster** — lords and throne (D75, journaled)",
        "add_lord_button": "Add a lord",
        "assign_queued_button": "Assign queued",
        "reassign_button": "Reassign",
        "eject_button": "Eject to queue",
        "swap_throne_button": "Swap throne",
        "player_id_label": "Player id",
        "display_name_label": "Display name",
        "role_label": "Role (king/lord)",
        "kingdom_label": "Kingdom name",
        "new_king_label": "New King id",
        "bad_role": "The role must be king or lord.",
        "missing_field": "Fill in the required fields.",
    },
    "fr": {
        "section_label": "🏰 Royaume",
        "section_description": "Saison et roster : recrutement, candidatures, quotas, fondation, seigneurs, trône",
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
        "roster_label": "**Roster** — seigneurs et trône (D75, journalisé)",
        "add_lord_button": "Ajouter un seigneur",
        "assign_queued_button": "Assigner la file",
        "reassign_button": "Réaffecter",
        "eject_button": "Éjecter vers la file",
        "swap_throne_button": "Échanger le trône",
        "player_id_label": "Identifiant joueur",
        "display_name_label": "Nom d'affichage",
        "role_label": "Rôle (king/lord)",
        "kingdom_label": "Nom du royaume",
        "new_king_label": "Id du nouveau Roi",
        "bad_role": "Le rôle doit être king ou lord.",
        "missing_field": "Remplis les champs requis.",
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


async def _run_panel_action(
    interaction: discord.Interaction,
    operation: str,
    payload: dict[str, Any],
    reason: str,
) -> None:
    """Run one panel operation through the D75 socle and answer."""
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
        elif operation in _ROSTER_FIELDS:
            await _run_roster_action(admin, operation, payload, reason, actor)
        else:  # pragma: no cover - the wire template only lets the panel actions through
            await _answer(interaction, strings["failed"].format(error=operation))
            return
    except KingdomsError as error:
        await _answer(interaction, strings["failed"].format(error=error.message))
        return
    await _answer(interaction, strings["done"])


async def _run_roster_action(
    admin: Any,
    operation: str,
    payload: dict[str, Any],
    reason: str,
    actor: dict[str, str],
) -> None:
    """Run one roster operation — the caller catches and answers."""
    if operation == "add_lord":
        await admin.add_lord(
            payload["player_id"], payload["display_name"], payload["role"], payload["kingdom"], reason=reason, **actor
        )
    elif operation == "assign_queued":
        await admin.assign_queued(
            payload["player_id"], payload["kingdom"], payload["role"], reason=reason, **actor
        )
    elif operation == "reassign":
        await admin.reassign(payload["player_id"], payload["kingdom"], reason=reason, **actor)
    elif operation == "eject":
        await admin.eject_to_queue(payload["player_id"], reason=reason, **actor)
    else:  # swap_throne — the five roster buttons of the wire template
        await admin.swap_throne(payload["kingdom"], payload["new_king_id"], reason=reason, **actor)


_ROSTER_FIELDS: dict[str, tuple[tuple[str, str], ...]] = {
    "add_lord": (
        ("player_id", "player_id_label"),
        ("display_name", "display_name_label"),
        ("role", "role_label"),
        ("kingdom", "kingdom_label"),
    ),
    "assign_queued": (("player_id", "player_id_label"), ("kingdom", "kingdom_label"), ("role", "role_label")),
    "reassign": (("player_id", "player_id_label"), ("kingdom", "kingdom_label")),
    "eject": (("player_id", "player_id_label"),),
    "swap_throne": (("kingdom", "kingdom_label"), ("new_king_id", "new_king_label")),
}
"""The roster modal fields per operation — text inputs, all required."""


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
        reason: discord.ui.TextInput[AdminReasonModal] = discord.ui.TextInput(
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
                field: discord.ui.TextInput[AdminReasonModal] = discord.ui.TextInput(
                    label=strings[label_key][:45], required=False, max_length=4
                )
                self.add_item(field)
                self._fields[key] = field
        elif operation == "foundation":
            for key, label_key in (("king", "foundation_king_label"), ("admin", "foundation_admin_label")):
                checkbox: discord.ui.TextInput[AdminReasonModal] = discord.ui.TextInput(
                    label=strings[label_key][:45], required=False, max_length=1
                )
                self.add_item(checkbox)
                self._fields[key] = checkbox
        elif operation in _ROSTER_FIELDS:
            for key, label_key in _ROSTER_FIELDS[operation]:
                roster_field: discord.ui.TextInput[AdminReasonModal] = discord.ui.TextInput(
                    label=strings[label_key][:45], required=True, max_length=100
                )
                self.add_item(roster_field)
                self._fields[key] = roster_field

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Gate the reason, parse the extras, run the operation."""
        strings = _strings(_locale(interaction))
        reason = str(self._fields["reason"].value or "").strip()
        if not reason:
            await _answer(interaction, strings["reason_label"])
            return
        payload = dict(self._payload)
        error = self._parse_extras(payload)
        if error is not None:
            await _answer(interaction, strings[error])
            return
        await _run_panel_action(interaction, self._operation, payload, reason)

    def _parse_extras(self, payload: dict[str, Any]) -> str | None:
        """Parse the per-operation fields; the error key, or None."""
        error = self._parse_season_extras(payload)
        if error is not None:
            return error
        return self._parse_roster_extras(payload)

    def _parse_season_extras(self, payload: dict[str, Any]) -> str | None:
        """Parse the quotas and foundation fields."""
        for key in ("kingdoms_count", "lords_per_kingdom"):
            if key not in self._fields:
                continue
            raw = str(self._fields[key].value or "").strip()
            if not raw:
                payload[key] = None
            elif raw.isdigit():
                payload[key] = int(raw)
            else:
                return "bad_number"
        for key in ("king", "admin"):
            if key not in self._fields:
                continue
            raw = str(self._fields[key].value or "").strip()
            if not raw:
                payload[key] = None
            elif raw in {"0", "1"}:
                payload[key] = raw == "1"
            else:
                return "bad_checkbox"
        return None

    def _parse_roster_extras(self, payload: dict[str, Any]) -> str | None:
        """Parse the roster text fields — all required, the role king/lord."""
        from kingdoms.mods.kingdoms.service import KING_ROLE, LORD_ROLE

        for key, _label in _ROSTER_FIELDS.get(self._operation, ()):
            if key not in self._fields:
                continue
            raw = str(self._fields[key].value or "").strip()
            if not raw:
                return "missing_field"
            if key == "role":
                if raw not in {"king", "lord"}:
                    return "bad_role"
                payload[key] = KING_ROLE if raw == "king" else LORD_ROLE
            else:
                payload[key] = raw
        return None


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


class KingdomsAdminRosterButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_ACTION_PREFIX}:roster-(?P<action>add-lord|assign-queued|reassign|eject|swap-throne)",
):
    """The roster buttons: add, assign, reassign, eject, throne swap."""

    _LABEL_KEYS: ClassVar[dict[str, str]] = {
        "add-lord": "add_lord_button",
        "assign-queued": "assign_queued_button",
        "reassign": "reassign_button",
        "eject": "eject_button",
        "swap-throne": "swap_throne_button",
    }
    _STYLES: ClassVar[dict[str, discord.ButtonStyle]] = {
        "add-lord": discord.ButtonStyle.success,
        "assign-queued": discord.ButtonStyle.primary,
        "reassign": discord.ButtonStyle.primary,
        "eject": discord.ButtonStyle.secondary,
        "swap-throne": discord.ButtonStyle.danger,
    }

    def __init__(self, action: str, label: str, style: discord.ButtonStyle) -> None:
        super().__init__(
            discord.ui.Button(label=label, custom_id=f"{_ACTION_PREFIX}:roster-{action}", style=style)
        )
        self.action = action

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomsAdminRosterButton:
        """Rebuild the button from the wire."""
        strings = _strings(_locale(interaction))
        action = match.group("action")
        return cls(action, strings[cls._LABEL_KEYS[action]], cls._STYLES[action])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the reason modal with this roster action's fields."""
        operation = self.action.replace("-", "_")
        await interaction.response.send_modal(AdminReasonModal(interaction, operation, {}))


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
    blocks.append(discord.ui.Separator())
    blocks.append(discord.ui.TextDisplay(strings["roster_label"]))
    roster_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    for action in ("add-lord", "assign-queued", "reassign", "eject", "swap-throne"):
        roster_row.add_item(
            KingdomsAdminRosterButton(
                action,
                strings[KingdomsAdminRosterButton._LABEL_KEYS[action]],
                KingdomsAdminRosterButton._STYLES[action],
            )
        )
    blocks.append(roster_row)
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
    bot.add_dynamic_items(KingdomsAdminRecruitmentSelect, KingdomsAdminActionButton, KingdomsAdminRosterButton)


def unregister_kingdoms_admin_section() -> None:
    """Drop the Royaume section (mod disabled or tests)."""
    from kingdoms.discord.admin_panel_mods import unregister_admin_mod_section

    unregister_admin_mod_section("kingdoms")
