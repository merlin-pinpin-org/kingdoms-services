"""The Kingdoms per-player profiles (kingdoms#138): salons-first v2.

Each enrolled applicant gets a **private profile channel** under the
Profils category — visible only to them and the admins:

- a pinned **state message** (pending / validated / refused), updated
  on every admin decision;
- a **Modify** flow: free edit before validation, "request a change"
  (admin-approved, relayed to the admin Demandes channel) after;
- **Stats** and **Succès** placeholders ("coming later");
- a **Leave the season** flow with a reason, relayed to the admin
  Demandes channel with decision buttons.

Smurf accounts declared at enrollment are shown only here and in the
candidature message — never in public channels.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import discord

logger = logging.getLogger("kingdoms.kingdom_profiles")

PROFILES_CATEGORY = "Profils"
PENDING_REQUESTS_CHANNEL = "Demandes"

STATE_MESSAGE_HEADER = "📋 Kingdoms — profil"
DECISION_ACCEPT_LEAVE_ID = "kingdoms:request:leave-approve"
DECISION_REFUSE_LEAVE_ID = "kingdoms:request:leave-refuse"
DECISION_ACCEPT_EDIT_ID = "kingdoms:request:edit-approve"
DECISION_REFUSE_EDIT_ID = "kingdoms:request:edit-refuse"

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "profile_title": "📋 {} — Kingdoms profile",
        "state": "Application status",
        "state_pending": "⏳ pending review",
        "state_validated": "✅ validated",
        "state_refused": "❌ refused",
        "role": "Role",
        "kingdom": "Kingdom",
        "queue": "Status",
        "queue_value": "waiting for a kingdom",
        "insight": "AoE II Insight",
        "game_id": "Game ID",
        "smurfs": "Declared smurf accounts",
        "smurfs_none": "none declared",
        "edit_button": "✏️ Modify my application",
        "edit_request_button": "✏️ Request a modification",
        "stats_button": "📊 Statistics",
        "success_button": "🏆 Achievements",
        "leave_button": "🚪 Leave the season",
        "coming_soon": "This feature will be available later. 🚧",
        "edit_pending": "You can still edit your application — it has not been reviewed yet.",
        "edit_validated": "Your application is validated: changes go through an admin review.",
        "edit_requested": "Your modification request has been sent to the admins.",
        "edit_request_title": "Modification request",
        "leave_modal_title": "Leave the season",
        "leave_reason_field": "Why are you leaving?",
        "leave_requested": "Your leave request has been sent to the admins.",
        "leave_request_title": "Leave request",
        "requests_note": "Use the buttons below to decide.",
        "leave_approved": "The player has been released from the season.",
        "leave_refused": "The leave request was refused.",
        "edit_approved": "The modification has been applied to the profile.",
        "edit_refused": "The modification request was refused.",
        "no_channel": "The profile channel could not be found.",
        "admin_section": "Administration",
        "remove_player_button": "🗑️ Remove a player / an application",
        "remove_player_title": "Remove a player",
        "remove_player_field": "Player (mention or id)",
        "remove_player_not_found": "No player found — mention them or give their id.",
        "remove_player_done": "{} has been removed from the season (roles revoked).",
        "reset_salons_button": "♻️ Reset the salons",
        "reset_confirm_title": "Reset the Kingdoms salons?",
        "reset_confirm_hint": "This deletes every kingdoms channel and category (including profiles).",
        "reset_confirm_button": "✅ Yes, reset",
        "reset_cancel_button": "❌ Cancel",
        "reset_cancelled": "Reset cancelled.",
        "reset_done": "Reset complete — {} items deleted. Run /kingdom to bootstrap again.",
        "reset_failed": "The reset failed — check the bot logs.",
        "deploy_button": "🚀 Deploy salons + panels",
        "deploy_confirm_title": "Deploy the Kingdoms salons + panels?",
        "deploy_confirm_hint": (
            "Missing channels are created, existing ones are adopted — then the panels are re-pinned."
        ),
        "deploy_confirm_button": "✅ Yes, deploy",
        "deploy_cancel_button": "❌ Cancel",
        "deploy_cancelled": "Deployment cancelled.",
        "deploy_done": "Deployment done — {} created, {} adopted. Panels re-pinned.",
        "sync_button": "🔄 Resync the panels",
        "sync_confirm_title": "Re-pin the Kingdoms panels?",
        "sync_confirm_hint": (
            "The pinned panel messages (Postuler, season status…) are re-posted. Salons are untouched."
        ),
        "sync_confirm_button": "✅ Yes, resync",
        "sync_cancel_button": "❌ Cancel",
        "sync_cancelled": "Resync cancelled.",
        "sync_done": "Panels re-synced — {} panels.",
        "status_button": "📊 Season status",
        "status_title": "📊 Kingdoms — Season II status",
        "status_kingdoms": "👑 Kingdoms",
        "status_queue": "⏳ Waiting players",
        "status_lords": "🪟 Enrolled players",
        "status_empty": "none yet",
        "assign_button": "📥 Assign a waiting player",
        "assign_modal_title": "Assign a waiting player",
        "assign_player_field": "Player (mention or id)",
        "assign_kingdom_field": "Kingdom name",
        "assign_done": "{} has been assigned to {}.",
        "assign_failed": "The assignment failed — {}",
        "add_kingdom_button": "+ Add a kingdom manually",
        "add_kingdom_modal_title": "Add a kingdom",
        "add_kingdom_field": "Kingdom name",
        "add_kingdom_done": "The kingdom **{}** has been added to the season.",
        "add_kingdom_failed": "The kingdom could not be added — {}",
        "launch_button": "▶️ Launch the season",
        "launch_confirm_title": "Launch a new season?",
        "launch_confirm_free_hint": "Free kingdoms: Kings found their own. Existing season data is wiped.",
        "launch_confirm_imposed_hint": "Imposed kingdoms: list the names, comma-separated (leave empty for free mode).",
        "launch_confirm_button": "✅ Yes, launch",
        "launch_cancel_button": "❌ Cancel",
        "launch_cancelled": "Season launch cancelled.",
        "launch_modal_title": "Launch the season",
        "launch_names_field": "Kingdom names (comma-separated, empty = free)",
        "launch_done_free": "Season launched — kingdoms are free to found.",
        "launch_done_imposed": "Season launched — imposed kingdoms: {}.",
        "launch_failed": "The launch failed — {}",
    },
    "fr": {
        "profile_title": "📋 {} — profil Kingdoms",
        "state": "Statut de la candidature",
        "state_pending": "⏳ en attente d'examen",
        "state_validated": "✅ validée",
        "state_refused": "❌ refusée",
        "role": "Rôle",
        "kingdom": "Royaume",
        "queue": "Statut",
        "queue_value": "en attente d'un royaume",
        "insight": "AoE II Insight",
        "game_id": "ID de jeu",
        "smurfs": "Comptes smurfs déclarés",
        "smurfs_none": "aucun déclaré",
        "edit_button": "✏️ Modifier ma candidature",
        "edit_request_button": "✏️ Demander une modification",
        "stats_button": "📊 Statistiques",
        "success_button": "🏆 Succès",
        "leave_button": "🚪 Quitter la saison",
        "coming_soon": "Cette fonctionnalité sera disponible ultérieurement. 🚧",
        "edit_pending": "Vous pouvez encore modifier votre candidature — elle n'a pas encore été examinée.",
        "edit_validated": "Votre candidature est validée : les modifications passent par un admin.",
        "edit_requested": "Votre demande de modification a été envoyée aux admins.",
        "edit_request_title": "Demande de modification",
        "leave_modal_title": "Quitter la saison",
        "leave_reason_field": "Pourquoi partez-vous ?",
        "leave_requested": "Votre demande de départ a été envoyée aux admins.",
        "leave_request_title": "Demande de départ",
        "requests_note": "Utilisez les boutons ci-dessous pour décider.",
        "leave_approved": "Le joueur a été libéré de la saison.",
        "leave_refused": "La demande de départ a été refusée.",
        "edit_approved": "La modification a été appliquée au profil.",
        "edit_refused": "La demande de modification a été refusée.",
        "no_channel": "Le salon de profil n'a pas pu être trouvé.",
        "admin_section": "Administration",
        "remove_player_button": "🗑️ Retirer un joueur / une candidature",
        "remove_player_title": "Retirer un joueur",
        "remove_player_field": "Joueur (mention ou id)",
        "remove_player_not_found": "Aucun joueur trouvé — mentionnez-le ou donnez son id.",
        "remove_player_done": "{} a été retiré de la saison (rôles révoqués).",
        "reset_salons_button": "♻️ Réinitialiser les salons",
        "reset_confirm_title": "Réinitialiser les salons Kingdoms ?",
        "reset_confirm_hint": "Cela supprime tous les salons et catégories kingdoms (profils inclus).",
        "reset_confirm_button": "✅ Oui, réinitialiser",
        "reset_cancel_button": "❌ Annuler",
        "reset_cancelled": "Réinitialisation annulée.",
        "reset_done": "Réinitialisation terminée — {} éléments supprimés. Relancez /kingdom pour recréer la structure.",
        "reset_failed": "La réinitialisation a échoué — consultez les logs du bot.",
        "deploy_button": "🚀 Déployer salons + panels",
        "deploy_confirm_title": "Déployer les salons + panels Kingdoms ?",
        "deploy_confirm_hint": (
            "Les salons manquants sont créés, les existants récupérés — puis les panels sont repinés."
        ),
        "deploy_confirm_button": "✅ Oui, déployer",
        "deploy_cancel_button": "❌ Annuler",
        "deploy_cancelled": "Déploiement annulé.",
        "deploy_done": "Déploiement terminé — {} créés, {} récupérés. Panels repinés.",
        "sync_button": "🔄 Resynchroniser les panels",
        "sync_confirm_title": "Re-piner les panels Kingdoms ?",
        "sync_confirm_hint": (
            "Les messages de panels épinglés (Postuler, statut de saison…) sont repostés."
            " Les salons ne sont pas modifiés."
        ),
        "sync_confirm_button": "✅ Oui, resynchroniser",
        "sync_cancel_button": "❌ Annuler",
        "sync_cancelled": "Resynchronisation annulée.",
        "sync_done": "Panels resynchronisés — {} panels.",
        "status_button": "📊 Statut de la saison",
        "status_title": "📊 Kingdoms — Statut de la Saison II",
        "status_kingdoms": "👑 Royaumes",
        "status_queue": "⏳ Joueurs en attente",
        "status_lords": "🪟 Joueurs inscrits",
        "status_empty": "aucun pour le moment",
        "assign_button": "📥 Affecter un joueur en attente",
        "assign_modal_title": "Affecter un joueur en attente",
        "assign_player_field": "Joueur (mention ou id)",
        "assign_kingdom_field": "Nom du royaume",
        "assign_done": "{} a été affecté à {}.",
        "assign_failed": "L'affectation a échoué — {}",
        "add_kingdom_button": "+ Ajouter un royaume manuellement",
        "add_kingdom_modal_title": "Ajouter un royaume",
        "add_kingdom_field": "Nom du royaume",
        "add_kingdom_done": "Le royaume **{}** a été ajouté à la saison.",
        "add_kingdom_failed": "Le royaume n'a pas pu être ajouté — {}",
        "launch_button": "▶️ Lancer la saison",
        "launch_confirm_title": "Lancer une nouvelle saison ?",
        "launch_confirm_free_hint": (
            "Royaumes libres : les Rois fondent le leur. Les données de la saison actuelle sont remises à zéro."
        ),
        "launch_confirm_imposed_hint": (
            "Royaumes imposés : listez les noms, séparés par des virgules (vide = mode libre)."
        ),
        "launch_confirm_button": "✅ Oui, lancer",
        "launch_cancel_button": "❌ Annuler",
        "launch_cancelled": "Lancement de saison annulé.",
        "launch_modal_title": "Lancer la saison",
        "launch_names_field": "Noms des royaumes (virgules, vide = libre)",
        "launch_done_free": "Saison lancée — les royaumes sont libres à fonder.",
        "launch_done_imposed": "Saison lancée — royaumes imposés : {}.",
        "launch_failed": "Le lancement a échoué — {}",
    },
}


def _strings(locale: str) -> dict[str, str]:
    return STRINGS["fr"] if str(locale).lower().startswith("fr") else STRINGS["en"]


def profile_channel_name(member: discord.abc.User) -> str:
    """Return the profile channel slug of one player (stable per member id)."""
    slug = re.sub(r"[^a-z0-9]+", "-", member.name.lower()).strip("-") or "joueur"
    return f"profil-{slug}"[:100]


def _find_channel_by_name(guild: discord.Guild, name: str) -> discord.TextChannel | None:
    from kingdoms.discord.kingdom_setup import _slug

    wanted = _slug(name)
    for channel in guild.text_channels:
        if _slug(channel.name) == wanted:
            return channel
    return None


def _profile_overwrites(guild: discord.Guild, member: discord.Member) -> dict[Any, discord.PermissionOverwrite]:
    """Build the profile channel permissions: the player + the bot + admins only."""
    overwrites: dict[Any, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        member: discord.PermissionOverwrite(view_channel=True, send_messages=True),
    }
    if guild.me is not None:
        overwrites[guild.me] = discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True)
    return overwrites


async def ensure_profile_channel(
    guild: discord.Guild,
    member: discord.Member,
) -> discord.TextChannel | None:
    """Create (or adopt) the private profile channel of one player."""
    from kingdoms.discord.kingdom_setup import _find_category

    category = _find_category(guild, PROFILES_CATEGORY)
    if category is None:
        category = await guild.create_category(
            PROFILES_CATEGORY,
            reason="kingdoms: player profiles",
            overwrites={},
            position=0,
        )
    name = profile_channel_name(member)
    existing = _find_channel_by_name(guild, name)
    if existing is not None:
        return existing
    channel = await guild.create_text_channel(
        name,
        reason=f"kingdoms: profile of {member.name}",
        category=category,
        overwrites=_profile_overwrites(guild, member),
    )
    return channel


def build_profile_message(
    locale: str,
    *,
    member_name: str,
    state: str,
    role_label: str,
    kingdom: str,
    queued: bool,
    insight: str,
    game_id: str,
    smurfs: tuple[str, ...] = (),
) -> tuple[str, discord.ui.View]:
    """Build the profile state message content + its action buttons."""
    strings = _strings(locale)
    lines = [
        f"# {strings['profile_title'].format(member_name)}",
        f"**{strings['state']}** : {state}",
        f"**{strings['role']}** : {role_label}",
    ]
    if queued:
        lines.append(f"**{strings['queue']}** : {strings['queue_value']}")
    else:
        lines.append(f"**{strings['kingdom']}** : {kingdom}")
    lines.extend(
        [
            f"**{strings['insight']}** : {insight}",
            f"**{strings['game_id']}** : {game_id}",
            f"**{strings['smurfs']}** : {', '.join(smurfs) if smurfs else strings['smurfs_none']}",
        ]
    )
    view = build_profile_view(locale, validated=state == strings["state_validated"])
    return "\n".join(lines), view


def build_profile_view(locale: str, *, validated: bool) -> discord.ui.View:
    """Build the profile action row: modify (or request), stats, success, leave."""
    strings = _strings(locale)

    async def on_edit(interaction: discord.Interaction) -> None:
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

    async def on_stats(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(strings["coming_soon"], ephemeral=True)

    async def on_success(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(strings["coming_soon"], ephemeral=True)

    async def on_leave(interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(_LeaveModal(locale))

    from kingdoms.discord.kingdom_persistent import KingdomProfileButton

    labels = {
        "edit": strings["edit_request_button"] if validated else strings["edit_button"],
        "stats": strings["stats_button"],
        "success": strings["success_button"],
        "leave": strings["leave_button"],
    }
    view = discord.ui.View(timeout=None)
    for key, label in labels.items():
        style = discord.ButtonStyle.primary if key == "edit" else discord.ButtonStyle.secondary
        view.add_item(KingdomProfileButton(key, label[:80], style))
    return view


class _LeaveModal(discord.ui.Modal):
    """The leave-the-season form: one mandatory reason field."""

    reason: discord.ui.TextInput[_LeaveModal] = discord.ui.TextInput(
        label="Why are you leaving?",
        style=discord.TextStyle.paragraph,
        max_length=500,
        required=True,
    )

    def __init__(self, locale: str) -> None:
        self.locale = "fr" if str(locale).lower().startswith("fr") else "en"
        strings = _strings(self.locale)
        self.reason.label = strings["leave_reason_field"][:45]
        super().__init__(title=strings["leave_modal_title"][:45], timeout=300)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        strings = _strings(self.locale)
        await _relay_request(
            interaction,
            self.locale,
            title=strings["leave_request_title"],
            custom_prefix="leave",
            reason=(self.reason.value or "").strip(),
        )
        await interaction.followup.send(strings["leave_requested"], ephemeral=True)


async def _relay_request(
    interaction: discord.Interaction,
    locale: str,
    *,
    title: str,
    custom_prefix: str,
    reason: str = "",
) -> None:
    """Post a player request into the admin Demandes channel."""
    strings = _strings(locale)
    guild = interaction.guild
    if guild is None:
        return
    channel = _find_channel_by_name(guild, PENDING_REQUESTS_CHANNEL)
    if channel is None:
        logger.warning("PROFILES: the %s channel is missing", PENDING_REQUESTS_CHANNEL)
        return
    lines = [f"# {title}", f"<@{interaction.user.id}>", f"*{strings['requests_note']}*"]
    if reason:
        lines.append(f"> {reason}")
    view = _request_decision_view(locale, custom_prefix)
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    await channel.send("\n".join(lines), view=view)


def _request_decision_view(locale: str, request_kind: str) -> discord.ui.View:
    """Build the admin decision buttons of one relayed request."""
    strings = _strings(locale)

    async def decide(interaction: discord.Interaction, approved: bool) -> None:
        permissions = getattr(interaction.user, "guild_permissions", None)
        if not permissions or not permissions.administrator:
            await interaction.response.send_message("Only admins can decide.", ephemeral=True)
            return
        key = "leave" if request_kind == "leave" else "edit"
        note = strings[f"{key}_approved"] if approved else strings[f"{key}_refused"]
        content = interaction.message.content if interaction.message is not None else ""
        await interaction.response.edit_message(content=f"{content}\n\n**{note}**", view=None)

    view = discord.ui.View(timeout=None)
    approve: discord.ui.Button[Any] = discord.ui.Button(
        label="✅", style=discord.ButtonStyle.success, custom_id=f"kingdoms:request:{request_kind}-approve"
    )
    refuse: discord.ui.Button[Any] = discord.ui.Button(
        label="❌", style=discord.ButtonStyle.danger, custom_id=f"kingdoms:request:{request_kind}-refuse"
    )

    async def on_approve(interaction: discord.Interaction) -> None:
        await decide(interaction, True)

    async def on_refuse(interaction: discord.Interaction) -> None:
        await decide(interaction, False)

    approve.callback = on_approve  # type: ignore[method-assign]
    refuse.callback = on_refuse  # type: ignore[method-assign]
    view.add_item(approve)
    view.add_item(refuse)
    return view
