"""The 🏰 Royaume admin panel (epic #214 phase 1.2, the first D75 surface).

The Royaume salon carries the roster-and-entities panel: every kingdom
operation an admin can run live — manual add, queue assignment,
reassignment (D17), throne swap, ejection, dissolution, manual kingdom
creation, admin rename, the recruitment/applications switches, the
free quotas and the foundation rights.

Every button rides the D75 foundation (``KingdomAdminService``): the
reason is **mandatory without exception** — collected by the action's
own modal before anything resolves — the action is journaled with its
before/after snapshots, and the journal section of the panel lists the
recent entries with their per-action rollback (2h window, once).

Restart-proofness follows the §3b state reconstruction contract: the
pinned panel message is found by its marker and edited in place, and
every action button is a ``DynamicItem`` whose whole state rides the
custom_id (``kingdoms:royaume:<action>``) — services resolve from the
live bot at click time. The modals and the journal view are
session-scoped ephemeral surfaces: they expire with the flow anyway.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, cast

import discord

from kingdoms.core.exceptions import KingdomsError
from kingdoms.discord.guards import require_admin

logger = logging.getLogger("kingdoms.royaume_panel")

__all__ = [
    "ROYAUME_CHANNEL",
    "ROYAUME_PANEL_MARKER",
    "RoyaumeActionButton",
    "RoyaumePanelSnapshot",
    "RoyaumePanelWiring",
    "build_panel_content",
    "build_panel_view",
    "refresh_royaume_panel",
    "register_royaume_panel_bot",
    "register_royaume_panel_wiring",
    "snapshot_from_services",
]

ROYAUME_CHANNEL = "royaume"
ROYAUME_PANEL_MARKER = "kingdoms:royaume:panel"
_BUTTON_PREFIX = "kingdoms:royaume"

KING = "roi"
LORD = "seigneur"
_ON = "on"

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "panel_title": "🏰 Royaume — kingdoms & roster",
        "panel_no_season": "No season is running — launch one from Gestion-saison.",
        "panel_hint": "Every action asks for a reason (D75) and lands in the journal.",
        "panel_kingdoms": "Kingdoms",
        "panel_queue": "Waiting queue",
        "panel_applications": "Applications",
        "panel_open": "open",
        "panel_closed": "closed",
        "recruit_open": "open",
        "recruit_closed": "closed",
        "panel_recruitment": "Recruitment",
        "panel_quotas": "Quotas",
        "panel_foundation": "Foundation rights",
        "panel_journal": "Journal",
        "panel_journal_empty": "No admin action journaled yet.",
        "panel_rolled_back": "rolled back",
        "section_roster": "Roster",
        "section_entities": "Entities",
        "section_switches": "Switches & quotas",
        "add_lord": "Add a lord",
        "assign_queued": "Assign queued",
        "reassign": "Reassign",
        "swap_throne": "Swap throne",
        "eject": "Eject to queue",
        "dissolve": "Dissolve",
        "create_kingdom": "Create kingdom",
        "rename_kingdom": "Rename kingdom",
        "recruitment_open": "Open recruitment",
        "recruitment_close": "Close recruitment",
        "applications_open": "Open applications",
        "applications_close": "Close applications",
        "quotas": "Quotas",
        "foundation": "Foundation rights",
        "journal": "Journal",
        "field_player": "Player (Discord ID)",
        "field_display_name": "Display name",
        "field_role": "Role (King or Lord)",
        "field_kingdom": "Kingdom name",
        "field_new_kingdom": "New kingdom name",
        "field_new_king": "New King (Discord ID)",
        "field_kingdoms_count": "Kingdoms quota (empty = default)",
        "field_lords_per_kingdom": "Lords per kingdom (empty = default)",
        "field_foundation_king": "King may found (on/off, empty = keep)",
        "field_foundation_admin": "Admin may found (on/off, empty = keep)",
        "field_reason": "Reason (mandatory, D75)",
        "reason_required": "A reason is mandatory (D75) — nothing was changed.",
        "service_unavailable": "The kingdoms service is not wired — the panel is read-only.",
        "denied": "Admins only.",
        "done_add_lord": "✅ {} added to **{}** as {}.",
        "done_assign_queued": "✅ {} assigned to **{}** as {}.",
        "done_reassign": "✅ {} moved to **{}**.",
        "done_swap_throne": "✅ {} is now the King of **{}**.",
        "done_eject": "✅ {} sent back to the waiting queue.",
        "done_dissolve": "✅ **{}** dissolved — territories to Gaïa, members queued.",
        "done_create_kingdom": "✅ Kingdom **{}** created.",
        "done_rename_kingdom": "✅ **{}** renamed to **{}**.",
        "done_recruitment_open": "✅ Recruitment opened for **{}**.",
        "done_recruitment_close": "✅ Recruitment closed for **{}**.",
        "done_applications_open": "✅ Applications open.",
        "done_applications_close": "✅ Applications closed.",
        "done_quotas": "✅ Quotas updated.",
        "done_foundation": "✅ Foundation rights updated.",
        "done_rollback": "✅ Action `{}` rolled back.",
        "confirm_dissolve": (
        "Dissolving **{}** returns its territories to Gaïa and queues every member (role kept). Confirm?"
    ),
        "confirm_yes": "Confirm",
        "confirm_no": "Cancel",
        "cancelled": "Cancelled — nothing was changed.",
        "journal_title": "📜 Journal — recent admin actions",
        "journal_line": "`{}` · {} · {} — *{}*",
        "rollback": "Rollback",
        "dm_add_lord": "You were added to the kingdom **{}** as {} by an admin. Reason: {}",
        "dm_assign_queued": "You were assigned to the kingdom **{}** as {} by an admin. Reason: {}",
        "dm_reassign": "You were moved to the kingdom **{}** by an admin. Reason: {}",
        "dm_swap_throne": "You are now the King of **{}** — total inheritance applies (D23). Reason: {}",
        "dm_eject": "You were sent back to the waiting queue by an admin (role kept). Reason: {}",
        "dm_dissolve": (
        "The kingdom **{}** was dissolved by an admin — you are back in the waiting queue, role kept. Reason: {}"
    ),
                "err_admin_reason_required": "❌ A reason is mandatory (D75).",
        "err_already_rolled_back": "❌ This action was already rolled back.",
        "err_rollback_window_closed": "❌ The 2-hour rollback window is closed.",
        "err_already_enrolled": "❌ The player is already enrolled this season.",
        "err_applications_closed": "❌ Applications are closed.",
        "err_foundation_closed": "❌ Foundation is closed.",
        "err_foundation_window_closed": "❌ The foundation rights are locked past the launch.",
        "err_kingdom_full": "❌ The kingdom is full (gel: nobody is ejected, D75).",
        "err_kingdom_limit": "❌ The kingdom quota is reached (gel, D75).",
        "err_kingdom_name_invalid": "❌ The kingdom name is invalid or taken.",
        "err_kingdom_not_found": "❌ Kingdom not found.",
        "err_no_season": "❌ No season is running.",
        "err_recruitment_closed": "❌ Recruitment is closed.",
        "err_reassign": "❌ Reassignment refused — {}",
        "err_eject_king": "❌ A King is ejected through a throne swap, never to the queue.",
        "err_throne_swap": "❌ Throne swap refused — {}",
"error_unexpected": "❌ Unexpected error: `{}`",
    },
    "fr": {
        "panel_title": "🏰 Royaume — royaumes & effectifs",
        "panel_no_season": "Aucune saison en cours — lancez-en une depuis Gestion-saison.",
        "panel_hint": "Toute action demande un motif (D75) et rejoint le journal.",
        "panel_kingdoms": "Royaumes",
        "panel_queue": "File d'attente",
        "panel_applications": "Candidatures",
        "panel_open": "ouvertes",
        "panel_closed": "fermées",
        "recruit_open": "ouvert",
        "recruit_closed": "fermé",
        "panel_recruitment": "Recrutement",
        "panel_quotas": "Quotas",
        "panel_foundation": "Droit de fondation",
        "panel_journal": "Journal",
        "panel_journal_empty": "Aucune action admin journalisée.",
        "panel_rolled_back": "annulée",
        "section_roster": "Effectifs",
        "section_entities": "Entités",
        "section_switches": "Commutateurs & quotas",
        "add_lord": "Ajouter un seigneur",
        "assign_queued": "Affecter la file",
        "reassign": "Réassigner",
        "swap_throne": "Échange de trône",
        "eject": "Éjecter vers la file",
        "dissolve": "Dissoudre",
        "create_kingdom": "Créer un royaume",
        "rename_kingdom": "Renommer un royaume",
        "recruitment_open": "Ouvrir le recrutement",
        "recruitment_close": "Fermer le recrutement",
        "applications_open": "Ouvrir les candidatures",
        "applications_close": "Fermer les candidatures",
        "quotas": "Quotas",
        "foundation": "Droit de fondation",
        "journal": "Journal",
        "field_player": "Joueur (ID Discord)",
        "field_display_name": "Nom d'affichage",
        "field_role": "Rôle (Roi ou Seigneur)",
        "field_kingdom": "Nom du royaume",
        "field_new_kingdom": "Nouveau nom du royaume",
        "field_new_king": "Nouveau Roi (ID Discord)",
        "field_kingdoms_count": "Quota royaumes (vide = défaut)",
        "field_lords_per_kingdom": "Seigneurs/royaume (vide = défaut)",
        "field_foundation_king": "Roi peut fonder (on/off, vide = inchangé)",
        "field_foundation_admin": "Admin peut fonder (on/off, vide = inchangé)",
        "field_reason": "Motif (obligatoire, D75)",
        "reason_required": "Un motif est obligatoire (D75) — rien n'a été modifié.",
        "service_unavailable": "Le service kingdoms n'est pas câblé — le panneau est en lecture seule.",
        "denied": "Réservé aux admins.",
        "done_add_lord": "✅ {} ajouté à **{}** en tant que {}.",
        "done_assign_queued": "✅ {} affecté à **{}** en tant que {}.",
        "done_reassign": "✅ {} déplacé vers **{}**.",
        "done_swap_throne": "✅ {} est désormais le Roi de **{}**.",
        "done_eject": "✅ {} renvoyé vers la file d'attente.",
        "done_dissolve": "✅ **{}** dissous — territoires rendus à Gaïa, membres en file.",
        "done_create_kingdom": "✅ Royaume **{}** créé.",
        "done_rename_kingdom": "✅ **{}** renommé en **{}**.",
        "done_recruitment_open": "✅ Recrutement ouvert pour **{}**.",
        "done_recruitment_close": "✅ Recrutement fermé pour **{}**.",
        "done_applications_open": "✅ Candidatures ouvertes.",
        "done_applications_close": "✅ Candidatures fermées.",
        "done_quotas": "✅ Quotas mis à jour.",
        "done_foundation": "✅ Droit de fondation mis à jour.",
        "done_rollback": "✅ Action `{}` annulée.",
        "confirm_dissolve": (
        "Dissoudre **{}** rend ses territoires à Gaïa et met chaque membre en file (rôle conservé). Confirmer ?"
    ),
        "confirm_yes": "Confirmer",
        "confirm_no": "Annuler",
        "cancelled": "Annulé — rien n'a été modifié.",
        "journal_title": "📜 Journal — actions admin récentes",
        "journal_line": "`{}` · {} · {} — *{}*",
        "rollback": "Annuler",
        "dm_add_lord": "Vous avez été ajouté au royaume **{}** en tant que {} par un admin. Motif : {}",
        "dm_assign_queued": "Vous avez été affecté au royaume **{}** en tant que {} par un admin. Motif : {}",
        "dm_reassign": "Vous avez été déplacé vers le royaume **{}** par un admin. Motif : {}",
        "dm_swap_throne": "Vous êtes désormais le Roi de **{}** — héritage total (D23). Motif : {}",
        "dm_eject": "Vous avez été renvoyé en file d'attente par un admin (rôle conservé). Motif : {}",
        "dm_dissolve": (
        "Le royaume **{}** a été dissous par un admin — vous êtes de retour en file, rôle conservé. Motif : {}"
    ),
                "err_admin_reason_required": "❌ Un motif est obligatoire (D75).",
        "err_already_rolled_back": "❌ Cette action a déjà été annulée.",
        "err_rollback_window_closed": "❌ La fenêtre d'annulation de 2 h est fermée.",
        "err_already_enrolled": "❌ Le joueur est déjà inscrit cette saison.",
        "err_applications_closed": "❌ Les candidatures sont fermées.",
        "err_foundation_closed": "❌ La fondation est fermée.",
        "err_foundation_window_closed": "❌ Le droit de fondation est verrouillé après le lancement.",
        "err_kingdom_full": "❌ Le royaume est complet (gel : personne n'est éjecté, D75).",
        "err_kingdom_limit": "❌ Le quota de royaumes est atteint (gel, D75).",
        "err_kingdom_name_invalid": "❌ Nom de royaume invalide ou déjà pris.",
        "err_kingdom_not_found": "❌ Royaume introuvable.",
        "err_no_season": "❌ Aucune saison en cours.",
        "err_recruitment_closed": "❌ Le recrutement est fermé.",
        "err_reassign": "❌ Réassignation refusée — {}",
        "err_eject_king": "❌ Un Roi passe par un échange de trône, jamais par la file.",
        "err_throne_swap": "❌ Échange de trône refusé — {}",
"error_unexpected": "❌ Erreur inattendue : `{}`",
    },
}


def _strings(locale: Any) -> dict[str, str]:
    """Pick the string set for an interaction locale (French default)."""
    return STRINGS["fr" if str(locale or "fr").lower().startswith("fr") else "en"]


def _interaction_locale(interaction: discord.Interaction) -> Any:
    """Read the interaction locale defensively (mocks, partial payloads)."""
    return getattr(interaction, "locale", None)


@dataclass(frozen=True, slots=True)
class RoyaumePanelWiring:
    """The services a reconstructed Royaume component resolves at click time."""

    admin_service: Any = None
    kingdoms_service: Any = None
    bot_admins: tuple[str, ...] = ()
    roles_service: Any = None


_WIRING_RESOLVER: Callable[[], RoyaumePanelWiring] | None = None


def _wiring() -> RoyaumePanelWiring:
    """Resolve the wiring: the live bot, or the static test wiring."""
    return _WIRING_RESOLVER() if _WIRING_RESOLVER is not None else RoyaumePanelWiring()


def register_royaume_panel_wiring(wiring: RoyaumePanelWiring) -> None:
    """Register a static wiring (tests and local runs)."""
    global _WIRING_RESOLVER

    def _resolve() -> RoyaumePanelWiring:
        return wiring

    _WIRING_RESOLVER = _resolve


def register_royaume_panel_bot(bot: Any) -> None:
    """Register the bot as the wiring source (resolved at click time)."""
    global _WIRING_RESOLVER

    def _resolve() -> RoyaumePanelWiring:
        return RoyaumePanelWiring(
            admin_service=getattr(bot, "kingdoms_admin_service", None),
            kingdoms_service=getattr(bot, "kingdoms_service", None),
            bot_admins=tuple(getattr(getattr(bot, "status_service", None), "bot_admins", ()) or ()),
            roles_service=getattr(bot, "roles_service", None),
        )

    _WIRING_RESOLVER = _resolve


# ----------------------------------------------------------------------
# the pinned panel message (marker-refresh contract)

@dataclass(frozen=True, slots=True)
class RoyaumePanelSnapshot:
    """The read-only state the pinned panel renders."""

    season_id: str | None
    kingdoms: tuple[tuple[str, bool, int], ...]
    queued: int
    applications_open: bool | None
    kingdoms_count: int | None
    lords_per_kingdom: int | None
    foundation_king: bool | None
    foundation_admin: bool | None
    actions: tuple[tuple[str, str, str, str, bool], ...]


async def snapshot_from_services(
    admin_service: Any,
    kingdoms_service: Any = None,
) -> RoyaumePanelSnapshot | None:
    """Read the panel state through the admin service (no captured state).

    Returns None when no season runs or the service is not wired; a
    read failure degrades to None as well — the panel never blocks.
    """
    kingdoms_service = kingdoms_service or getattr(admin_service, "_kingdoms", None)
    if kingdoms_service is None:
        return None
    try:
        kingdoms = [k for k in await kingdoms_service.kingdoms() if not k.is_gaia]
        lords = await kingdoms_service.lords()
        season = await kingdoms_service.current_season()
        if season is None:
            return None
        actions = await admin_service.journal.actions(season_id=season.id) if admin_service else []
        return RoyaumePanelSnapshot(
            season_id=season.id,
            kingdoms=tuple(
                (
                    k.name,
                    bool(k.recruitment_open),
                    sum(1 for lord in lords if lord.kingdom_id == k.id and not lord.left and not lord.in_queue),
                )
                for k in kingdoms
            ),
            queued=sum(1 for lord in lords if not lord.left and lord.in_queue),
            applications_open=bool(getattr(season, "applications_open", False)),
            kingdoms_count=getattr(season, "kingdoms_count_override", None),
            lords_per_kingdom=getattr(season, "lords_per_kingdom_override", None),
            foundation_king=bool(getattr(season, "foundation_king", None)),
            foundation_admin=bool(getattr(season, "foundation_admin", None)),
            actions=tuple(
                (a.id, a.action_type, a.actor_name, a.reason, bool(a.rolled_back)) for a in actions[:10]
            ),
        )
    except Exception:
        logger.warning("ROYAUME PANEL: snapshot read failed", exc_info=True)
        return None


def build_panel_content(snapshot: RoyaumePanelSnapshot | None, locale: str | None) -> str:
    """Render the pinned Royaume panel message."""
    strings = _strings(locale)
    if snapshot is None:
        return "\n".join([f"# {strings['panel_title']}", strings["panel_no_season"], ROYAUME_PANEL_MARKER])
    kingdom_lines = [
        f"• **{name}** — {count} · {strings['panel_recruitment']} "
        f"{strings['recruit_open' if open_ else 'recruit_closed']}"
        for name, open_, count in snapshot.kingdoms
    ] or [f"_{strings['panel_journal_empty']}_"]
    quota_line = f"{snapshot.kingdoms_count if snapshot.kingdoms_count else '—'} / " \
                 f"{snapshot.lords_per_kingdom if snapshot.lords_per_kingdom else '—'}"
    foundation_line = (
        f"Roi {'✅' if snapshot.foundation_king else '❌'} · Admin {'✅' if snapshot.foundation_admin else '❌'}"
    )
    journal_lines = [
        strings["journal_line"].format(aid, action, actor, reason)
        + (f" *({strings['panel_rolled_back']})*" if rolled else "")
        for aid, action, actor, reason, rolled in snapshot.actions
    ] or [f"_{strings['panel_journal_empty']}_"]
    lines = [
        f"# {strings['panel_title']}",
        f"**{strings['panel_kingdoms']}** ({len(snapshot.kingdoms)})",
        *kingdom_lines,
        f"**{strings['panel_queue']}** : {snapshot.queued}",
        (
            f"**{strings['panel_applications']}** : "
            f"{strings['panel_open'] if snapshot.applications_open else strings['panel_closed']}"
        ),
        f"**{strings['panel_quotas']}** : {quota_line}",
        f"**{strings['panel_foundation']}** : {foundation_line}",
        f"**{strings['panel_journal']}**",
        *journal_lines[:5],
        f"*{strings['panel_hint']}*",
        ROYAUME_PANEL_MARKER,
    ]
    return "\n".join(lines)


def build_panel_view(locale: str | None) -> discord.ui.View:
    """Build the action rows of the pinned panel (restart-proof buttons)."""
    strings = _strings(locale)
    view = discord.ui.View(timeout=None)
    for row in (
        ("add_lord", "assign_queued", "reassign", "swap_throne", "eject"),
        ("dissolve", "create_kingdom", "rename_kingdom"),
        ("recruitment_open", "recruitment_close", "applications_open", "applications_close"),
        ("quotas", "foundation", "journal"),
    ):
        for action in row:
            view.add_item(RoyaumeActionButton(action, strings[action]))
    return view


async def _refresh_marker_message(
    channel: discord.abc.Messageable | None,
    content: str,
    view: discord.ui.View | None,
) -> bool:
    """Post (or edit in place) the single marker message of a channel."""
    if channel is None:
        return False
    for message in list(getattr(channel, "messages", []) or []):
        if ROYAUME_PANEL_MARKER in (getattr(message, "content", "") or ""):
            try:
                await message.edit(content=content, view=view)
            except Exception:
                logger.warning("ROYAUME PANEL: refresh failed", exc_info=True)
            return True
    await channel.send(content, view=view)
    return True


async def refresh_royaume_panel(
    guild: discord.Guild,
    locale: str | None,
    admin_service: Any = None,
) -> bool:
    """Post (or refresh) the pinned Royaume panel in the royaume salon."""
    from kingdoms.discord.kingdom_setup import _slug

    snapshot = await snapshot_from_services(admin_service or _wiring().admin_service)
    channel = next((c for c in guild.text_channels if _slug(c.name) == ROYAUME_CHANNEL), None)
    return await _refresh_marker_message(
        channel, build_panel_content(snapshot, locale), build_panel_view(locale)
    )


# ----------------------------------------------------------------------
# the action buttons and modals (D75: reason rides every modal)

@dataclass(frozen=True, slots=True)
class _FieldSpec:
    """One modal input of a Royaume action."""

    key: str
    label: str
    required: bool = True
    placeholder: str = ""


_FIELDS: dict[str, tuple[_FieldSpec, ...]] = {}


def _fields_for(action: str, strings: dict[str, str]) -> tuple[_FieldSpec, ...]:
    """Resolve the modal fields of one action (cached per locale-free key)."""
    if action in _FIELDS:
        return _FIELDS[action]
    shapes: dict[str, tuple[tuple[str, bool], ...]] = {
        "add_lord": (("player", True), ("display_name", True), ("role", True), ("kingdom", True), ("reason", True)),
        "assign_queued": (("player", True), ("kingdom", True), ("role", True), ("reason", True)),
        "reassign": (("player", True), ("kingdom", True), ("reason", True)),
        "swap_throne": (("kingdom", True), ("new_king", True), ("reason", True)),
        "eject": (("player", True), ("reason", True)),
        "dissolve": (("kingdom", True), ("reason", True)),
        "create_kingdom": (("kingdom", True), ("reason", True)),
        "rename_kingdom": (("kingdom", True), ("new_kingdom", True), ("reason", True)),
        "recruitment_open": (("kingdom", True), ("reason", True)),
        "recruitment_close": (("kingdom", True), ("reason", True)),
        "applications_open": (("reason", True),),
        "applications_close": (("reason", True),),
        "quotas": (
            ("kingdoms_count", False),
            ("lords_per_kingdom", False),
            ("reason", True),
        ),
        "foundation": (("foundation_king", False), ("foundation_admin", False), ("reason", True)),
        "rollback": (("reason", True),),
    }
    fields = tuple(
        _FieldSpec(
            key=key,
            label=strings[f"field_{key}"][:45],
            required=required,
        )
        for key, required in shapes[action]
    )
    _FIELDS[action] = fields
    return fields


def build_action_modal(action: str, locale: str | None) -> discord.ui.Modal:
    """Build one action's modal — the reason field is always present (D75).

    The modal class is assembled per action from its field specs (the
    shapes differ per action; Discord caps a modal at five inputs).
    """
    strings = _strings(locale)
    fields = _fields_for(action, strings)
    namespace: dict[str, Any] = {}
    for index, field in enumerate(fields):
        namespace[f"field_{index}"] = discord.ui.TextInput[Any](
            label=field.label,
            placeholder=field.key[:100],
            max_length=200,
            required=field.required,
        )
    namespace["on_submit"] = _make_on_submit(action, tuple(field.key for field in fields))
    modal_cls = type(f"_RoyaumeModal_{action}", (discord.ui.Modal,), namespace)
    return cast(discord.ui.Modal, modal_cls(title=strings[action][:45], timeout=300))


def _make_on_submit(
    action: str,
    keys: tuple[str, ...],
) -> Callable[[discord.ui.Modal, discord.Interaction], Awaitable[None]]:
    """Bind one action's submit handler to its modal fields."""

    async def on_submit(self: discord.ui.Modal, interaction: discord.Interaction) -> None:
        """Collect the modal values and run the action through the service."""
        values = {
            key: str(getattr(self, f"field_{index}").value or "").strip()
            for index, key in enumerate(keys)
        }
        carried = getattr(self, "_royaume_action_id", None)
        if carried:
            values["action_id"] = str(carried)
        await run_action(interaction, action, values)

    return on_submit


async def send_journal_view(interaction: discord.Interaction, admin_service: Any) -> None:
    """Answer with the ephemeral journal listing and its rollback buttons.

    The listing is session-scoped (an ephemeral message): the rollback
    buttons carry the action ids in their live closures — the pinned
    panel itself stays the durable surface.
    """
    strings = _strings(_interaction_locale(interaction))
    snapshot = await snapshot_from_services(admin_service)
    entries = snapshot.actions if snapshot else ()
    lines = [f"## {strings['journal_title']}"]
    view = discord.ui.View(timeout=600)
    if not entries:
        lines.append(f"_{strings['panel_journal_empty']}_")
    for index, (action_id, action_type, actor_name, reason, rolled_back) in enumerate(entries[:5]):
        line = strings["journal_line"].format(action_id, action_type, actor_name, reason)
        lines.append(line + (f" *({strings['panel_rolled_back']})*" if rolled_back else ""))
        if not rolled_back:
            view.add_item(_RollbackButton(action_id, strings["rollback"], index))
    await interaction.response.send_message("\n".join(lines), view=view, ephemeral=True)


class _RollbackButton(discord.ui.Button[Any]):
    """One journal entry's rollback button (session-scoped, D75)."""

    def __init__(self, action_id: str, label: str, row: int) -> None:
        """Bind the button to one journaled action."""
        super().__init__(style=discord.ButtonStyle.danger, label=label[:80], row=row)
        self.action_id = action_id

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the mandatory-reason modal, then roll the action back."""
        if not await _require_panel_access(interaction):
            return
        modal: Any = build_action_modal("rollback", interaction.locale)
        modal._royaume_action_id = self.action_id
        await interaction.response.send_modal(modal)


async def _require_panel_access(interaction: discord.Interaction) -> bool:
    """Enforce the click-time admin guard (the guard answers denials)."""
    wiring = _wiring()
    return await require_admin(interaction, wiring.bot_admins, wiring.roles_service)


class RoyaumeActionButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_BUTTON_PREFIX}:(?P<action>[a-z_]+)",
):
    """One pinned-panel action button — stateless, restart-proof (§3b)."""

    def __init__(self, action: str, label: str) -> None:
        """Build the button for one action key."""
        destructive = action in {"dissolve", "eject"}
        super().__init__(
            discord.ui.Button(
                style=discord.ButtonStyle.danger if destructive else discord.ButtonStyle.secondary,
                label=label[:80],
                custom_id=f"{_BUTTON_PREFIX}:{action}",
            )
        )
        self.action = action

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button[Any],
        match: Any,
    ) -> RoyaumeActionButton:
        """Rebuild the button from the wire (labels localize at click time)."""
        return cls(match["action"], _strings(_interaction_locale(interaction))[match["action"]])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Guard the click, then open the action modal — or the journal view."""
        if not await _require_panel_access(interaction):
            return
        wiring = _wiring()
        if wiring.admin_service is None:
            await interaction.response.send_message(
                _strings(_interaction_locale(interaction))["service_unavailable"], ephemeral=True
            )
            return
        if self.action == "journal":
            await send_journal_view(interaction, wiring.admin_service)
            return
        await interaction.response.send_modal(build_action_modal(self.action, interaction.locale))


async def run_action(
    interaction: discord.Interaction,
    action: str,
    values: dict[str, str],
) -> None:
    """Execute one journaled admin action and answer before/after (D75).

    Every path is journaled by the service itself; the panel only
    localizes the outcome, announces Géopolitique and DMs the target
    player (fallback: the royaume salon).
    """
    strings = _strings(_interaction_locale(interaction))
    wiring = _wiring()
    admin = wiring.admin_service
    if admin is None:
        await interaction.response.send_message(strings["service_unavailable"], ephemeral=True)
        return
    actor_id = str(interaction.user.id)
    actor_name = interaction.user.display_name or actor_id
    reason = values.get("reason", "")
    if not reason:
        await interaction.response.send_message(strings["reason_required"], ephemeral=True)
        return
    actor = {"actor_id": actor_id, "actor_name": actor_name}
    await interaction.response.defer(ephemeral=True)
    try:
        message, dm_player_id, dm_template = await _dispatch(admin, action, values, reason, actor)
    except KingdomsError as error:
        key = (getattr(error, "message_key", "") or "").split(".")[-1]
        text = strings.get(f"err_{key}", strings["error_unexpected"].format(f"{type(error).__name__}: {error}"))
        await interaction.followup.send(text, ephemeral=True)
        return
    except Exception as error:  # the panel never crashes the click
        logger.exception("ROYAUME PANEL: action %s failed", action)
        await interaction.followup.send(
            strings["error_unexpected"].format(f"{type(error).__name__}: {error}"), ephemeral=True
        )
        return
    await interaction.followup.send(strings[message[0]].format(*message[1:]), ephemeral=True)
    if dm_player_id and dm_template:
        await _notify_player(interaction, dm_player_id, dm_template, values)
    await refresh_royaume_panel_from_interaction(interaction)


async def _dispatch(
    admin: Any,
    action: str,
    values: dict[str, str],
    reason: str,
    actor: dict[str, str],
) -> tuple[tuple[str, tuple[str, ...]], str, str]:
    """Run one action on the admin service; return (message, dm id, dm key)."""
    role = _role_of(values.get("role", ""))
    if action == "add_lord":
        lord = await admin.add_lord(
            values["player"], values["display_name"], role, values["kingdom"], reason=reason, **actor
        )
        return ("done_add_lord", (values["display_name"], values["kingdom"], role)), lord.id, "dm_add_lord"
    if action == "assign_queued":
        lord = await admin.assign_queued(
            values["player"], values["kingdom"], role, reason=reason, **actor
        )
        return ("done_assign_queued", (values["player"], values["kingdom"], role)), lord.id, "dm_assign_queued"
    if action == "reassign":
        lord = await admin.reassign(values["player"], values["kingdom"], reason=reason, **actor)
        return ("done_reassign", (values["player"], values["kingdom"])), lord.id, "dm_reassign"
    if action == "swap_throne":
        new_king, _ = await admin.swap_throne(
            values["kingdom"], values["new_king"], reason=reason, **actor
        )
        return ("done_swap_throne", (values["new_king"], values["kingdom"])), new_king.id, "dm_swap_throne"
    if action == "eject":
        lord = await admin.eject_to_queue(values["player"], reason=reason, **actor)
        return ("done_eject", (values["player"],)), lord.id, "dm_eject"
    if action == "dissolve":
        await admin.dissolve_kingdom(values["kingdom"], reason=reason, **actor)
        return ("done_dissolve", (values["kingdom"],)), "", ""
    if action == "create_kingdom":
        await admin.create_kingdom(values["kingdom"], reason=reason, **actor)
        return ("done_create_kingdom", (values["kingdom"],)), "", ""
    if action == "rename_kingdom":
        await admin.rename_kingdom(
            values["kingdom"], values["new_kingdom"], reason=reason, **actor
        )
        return ("done_rename_kingdom", (values["kingdom"], values["new_kingdom"])), "", ""
    return await _dispatch_policy(admin, action, values, reason, actor)


async def _dispatch_policy(
    admin: Any,
    action: str,
    values: dict[str, str],
    reason: str,
    actor: dict[str, str],
) -> tuple[tuple[str, tuple[str, ...]], str, str]:
    """Run one policy action (switches, quotas, foundation, rollback)."""
    if action in {"recruitment_open", "recruitment_close"}:
        await admin.set_recruitment(
            values["kingdom"], open=action == "recruitment_open", reason=reason, **actor
        )
        key = "done_recruitment_open" if action == "recruitment_open" else "done_recruitment_close"
        return (key, (values["kingdom"],)), "", ""
    if action in {"applications_open", "applications_close"}:
        await admin.set_applications(open=action == "applications_open", reason=reason, **actor)
        key = "done_applications_open" if action == "applications_open" else "done_applications_close"
        return (key, ()), "", ""
    if action == "quotas":
        await admin.set_quotas(
            kingdoms_count=_optional_int(values.get("kingdoms_count")),
            lords_per_kingdom=_optional_int(values.get("lords_per_kingdom")),
            reason=reason,
            **actor,
        )
        return ("done_quotas", ()), "", ""
    if action == "rollback":
        action_id = values.get("action_id", "")
        await admin.rollback(action_id, reason=reason, **actor)
        return ("done_rollback", (action_id,)), "", ""
    if action == "foundation":
        await admin.set_foundation_rights(
            king=_optional_bool(values.get("foundation_king")),
            admin=_optional_bool(values.get("foundation_admin")),
            reason=reason,
            **actor,
        )
        return ("done_foundation", ()), "", ""
    raise ValueError(f"unknown Royaume panel action: {action}")


def _role_of(raw: str) -> Any:
    """Resolve a modal role word to the LordRole enum (D75 — Roi/Seigneur)."""
    from kingdoms.mods.kingdoms.models import LordRole

    word = raw.strip().lower()
    if word in {"king", "roi", "r"}:
        return LordRole.KING
    if word in {"lord", "seigneur", "s"}:
        return LordRole.LORD
    raise ValueError(f"unknown role: {raw}")


def _optional_int(raw: str | None) -> int | None:
    """Parse an optional quota input (empty = the config default)."""
    if not (raw or "").strip():
        return None
    return int(raw.strip())


def _optional_bool(raw: str | None) -> bool | None:
    """Parse an optional on/off input (empty = keep the current value)."""
    word = (raw or "").strip().lower()
    if not word:
        return None
    return word in {_ON, "oui", "yes", "1", "true"}


async def _notify_player(
    interaction: discord.Interaction,
    player_id: str,
    dm_key: str,
    values: dict[str, str],
) -> None:
    """DM the target player the D75 verdict; fall back to the royaume salon."""
    strings = _strings(_interaction_locale(interaction))
    text = strings[dm_key].format(*_dm_fields(dm_key, values))
    sent = False
    guild = interaction.guild
    if guild is not None and player_id.isdigit():
        member = guild.get_member(int(player_id))
        if member is not None:
            try:
                await member.send(text)
                sent = True
            except Exception:
                logger.info("ROYAUME PANEL: DM to %s failed — falling back", player_id)
    if not sent:
        channel = interaction.channel
        if isinstance(channel, discord.abc.Messageable):
            try:
                await channel.send(text)
            except Exception:
                logger.warning("ROYAUME PANEL: fallback notification failed", exc_info=True)


def _dm_fields(dm_key: str, values: dict[str, str]) -> tuple[str, ...]:
    """Pick the format fields of one DM template from the modal values."""
    role = values.get("role", "")
    if dm_key in {"dm_add_lord", "dm_assign_queued"}:
        return (values.get("kingdom", ""), role, values.get("reason", ""))
    if dm_key in {"dm_reassign", "dm_swap_throne", "dm_eject"}:
        return (values.get("kingdom", ""), values.get("reason", ""))
    if dm_key == "dm_dissolve":
        return (values.get("kingdom", ""), values.get("reason", ""))
    return ()


async def refresh_royaume_panel_from_interaction(interaction: discord.Interaction) -> None:
    """Refresh the pinned panel after an action (best effort, never blocks)."""
    guild = interaction.guild
    if guild is None:
        return
    try:
        await refresh_royaume_panel(guild, str(interaction.locale or "fr"), _wiring().admin_service)
    except Exception:
        logger.warning("ROYAUME PANEL: post-action refresh failed", exc_info=True)
