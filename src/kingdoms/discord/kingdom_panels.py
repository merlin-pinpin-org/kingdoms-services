"""The Kingdoms salons-first panels (kingdoms#138): enrollment workflow.

Three panels on top of the bootstrapped structure:

- **Postuler** (open to all) — a pinned button opens an ephemeral,
  per-player enrollment flow with no free-text role: a select menu
  offers Seigneur or Roi; a Seigneur then picks a declared kingdom
  from a list (or joins the waiting queue, for an admin to assign
  later), a Roi types a kingdom name; both then give their AoE II
  Insight link + game ID and confirm with a rules-acceptance button
  (no typing);
- **Candidatures** (admin only) — each submitted application lands as
  a message with admin-only buttons ✅ validated / ⏳ pending / ❌
  refused; validation assigns the kingdoms_lord or kingdoms_king mod
  role to the player;
- **Paramètres** (admin only) — the mod's own season actions; language
  and timezone live in the platform core (bot-admin).

The mod declares its roles in ``config/mods/kingdoms.yaml``
(kingdoms_king, kingdoms_lord); the panels assign them through the
ModRolesService — never raw Discord role IDs. Panels degrade when the
stores are not wired (local runs): the buttons answer with a note.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import discord
from discord import app_commands

from kingdoms.core.services.logs import LogService
from kingdoms.core.services.roles import ModRolesService
from kingdoms.discord.commands_i18n import localized
from kingdoms.discord.ui import (
    GREEN,
)
from kingdoms.mods.kingdoms.snapshot import season_label

logger = logging.getLogger("kingdoms.kingdom_panels")

KINGDOM_MOD = "kingdoms"
ROLE_LORD = "kingdoms_lord"
ROLE_KING = "kingdoms_king"

APPLY_BUTTON_ID = "kingdoms:apply:open"
ROLE_SELECT_ID = "kingdoms:apply:role"
KINGDOM_SELECT_ID = "kingdoms:apply:kingdom"
DECISION_APPROVE_ID = "kingdoms:candidature:approve"
DECISION_PENDING_ID = "kingdoms:candidature:pending"
DECISION_REFUSE_ID = "kingdoms:candidature:refuse"

QUEUE_VALUE = "__queue__"
PANEL_MARKER = "kingdoms:panel:postuler"
SETTINGS_PANEL_MARKER = "kingdoms:panel:parametres"
SEASON_STATUS_MARKER = "kingdoms:season:status"
SEASON_STATUS_CHANNEL = "saison"
UPDATE_CHANNEL = "update"
CHANGELOG_MARKER = "kingdoms:update:changelog"


async def refresh_changelog(guild: discord.Guild, locale: str) -> bool:
    """Post (or refresh) the kingdoms changelog message in the Update channel."""
    channel = next((c for c in guild.text_channels if c.name.lower() == UPDATE_CHANNEL), None)
    if channel is None:
        return False
    fr = str(locale).lower().startswith("fr")
    items = (
        "🛠️ Panneau admin enrichi : déploiement des salons + panels, resynchronisation, "
        "statut de la saison, affectation des joueurs en attente, ajout manuel de royaume — "
        "les actions sensibles demandent une confirmation."
        if fr
        else "🛠️ Richer admin panel: deploy salons + panels, resync, season status, "
        "assign waiting players, add a kingdom manually — sensitive actions ask for confirmation."
    )
    content = f"{items}\n{CHANGELOG_MARKER}"
    for message in list(getattr(channel, "messages", [])):
        if CHANGELOG_MARKER in (message.content or ""):
            try:
                await message.edit(content=content)
            except Exception:
                logger.warning("KINGDOM PANELS: changelog refresh failed", exc_info=True)
            return True
    await channel.send(content)
    return True


async def post_update_note(guild: discord.Guild, locale: str, note: str) -> bool:
    """Post a dated changelog note in the Update channel."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    channel = next((c for c in guild.text_channels if c.name.lower() == UPDATE_CHANNEL), None)
    if channel is None:
        return False
    try:
        tz = ZoneInfo("Europe/Paris")
    except Exception:
        tz = None
    now = datetime.now(tz) if tz else datetime.now()
    stamp = now.strftime("%d/%m/%Y %H:%M")
    await channel.send(f"**🕘 Update — {stamp}**\n{note}")
    return True

INSIGHT_URL = re.compile(r"^https?://.+", re.IGNORECASE)
GAME_ID = re.compile(r"^\d{6,20}$")

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "apply_button": "📋 Enroll",
        "apply_title": "Kingdoms enrollment",
        "choose_role": "Choose your role",
        "choose_kingdom": "Join a kingdom",
        "queue_option": "⏳ Wait for a kingdom",
        "queue_description": "Wait for kingdoms to be created — an admin will assign you later.",
        "no_kingdoms_hint": "No kingdom exists yet — you can wait in the queue.",
        "role_king": "👑 King",
        "role_lord": "🎖️ Lord",
        "kingdom_field": "Kingdom name",
        "kingdom_placeholder": "My Kingdom",
        "insight_field": "AoE II Insight link",
        "insight_placeholder": "https://www.aoe2insight.com/…",
        "game_id_field": "Game ID",
        "game_id_placeholder": "12345678",
        "smurfs_field": "Other AoE II Insight accounts (smurfs)",
        "smurfs_placeholder": "https://www.aoe2insight.com/… (one per line, optional)",
        "summary_title": "📋 Review your application",
        "summary_role": "Role",
        "summary_kingdom": "Kingdom",
        "summary_queue": "Status",
        "queue_value": "waiting for a kingdom",
        "summary_insight": "AoE II Insight",
        "summary_game_id": "Game ID",
        "summary_smurfs": "Declared smurf accounts",
        "rules_label": "I have read and I accept the rules",
        "submit": "✅ I accept the rules — send my application",
        "cancel": "Cancel",
        "bad_insight": "The AoE II Insight link must be a valid URL.",
        "bad_game_id": "The game ID must be 6 to 20 digits.",
        "king_needs_name": "A King must suggest a kingdom name.",
        "cancelled": "Application cancelled.",
        "sent": "Your application has been sent — an admin will review it.",
        "candidature_title": "📋 New enrollment application",
        "candidature_role": "Requested role",
        "candidature_kingdom": "Kingdom",
        "candidature_queue": "Status",
        "candidature_insight": "AoE II Insight",
        "candidature_game_id": "Game ID",
        "decided": "The application has been marked {}.",
        "approved": "✅ validated",
        "pending": "⏳ pending",
        "refused": "❌ refused",
        "role_assigned": "The {} role has been assigned.",
        "no_service": "The enrollment service is not available right now.",
        "enroll_failed": "Enrollment failed ({}).",
        "enroll_queued": "The player is now waiting in the queue.",
        "enrolled_kingdom": "Enrolled in kingdom {}.",
        "settings_title": "⚙️ Kingdoms — settings",
        "welcome_title": "🏰 Welcome to Kingdoms — Season II!",
        "welcome_body": (
            "Your enrollment has been validated. Read the rules, present yourself in Présentation,"
            " and good luck on the battlefield!"
        ),
        "welcome_fallback": "(Sent here because your private messages are closed.)",
        "season_status_title": "📊 Kingdoms — Season II status",
        "season_status_kingdoms": "👑 Kingdoms",
        "season_status_queue": "⏳ Waiting players",
        "season_status_empty": "none yet",
        "season_status_hint": "This message is refreshed automatically by the bot.",
    },
    "fr": {
        "apply_button": "📋 S'inscrire",
        "apply_title": "Inscription Kingdoms",
        "choose_role": "Choisissez votre rôle",
        "choose_kingdom": "Rejoindre un royaume",
        "queue_option": "⏳ En attente",
        "queue_description": "Attendre la création des royaumes — un admin vous affectera plus tard.",
        "no_kingdoms_hint": "Aucun royaume n'existe encore — vous pouvez vous mettre en attente.",
        "role_king": "👑 Roi",
        "role_lord": "🎖️ Seigneur",
        "kingdom_field": "Nom du royaume",
        "kingdom_placeholder": "Mon Royaume",
        "insight_field": "Lien AoE II Insight",
        "insight_placeholder": "https://www.aoe2insight.com/…",
        "game_id_field": "ID de jeu",
        "game_id_placeholder": "12345678",
        "smurfs_field": "Autres comptes AoE II Insight (smurfs)",
        "smurfs_placeholder": "https://www.aoe2insight.com/… (une par ligne, facultatif)",
        "summary_title": "📋 Vérifiez votre candidature",
        "summary_role": "Rôle",
        "summary_kingdom": "Royaume",
        "summary_queue": "Statut",
        "queue_value": "en attente d'un royaume",
        "summary_insight": "AoE II Insight",
        "summary_game_id": "ID de jeu",
        "summary_smurfs": "Comptes smurfs déclarés",
        "rules_label": "J'ai lu et j'accepte les règles",
        "submit": "✅ J'accepte les règles — envoyer ma candidature",
        "cancel": "Annuler",
        "bad_insight": "Le lien AoE II Insight doit être une URL valide.",
        "bad_game_id": "L'ID de jeu doit contenir 6 à 20 chiffres.",
        "king_needs_name": "Un Roi doit suggérer un nom de royaume.",
        "cancelled": "Candidature annulée.",
        "sent": "Votre candidature a été envoyée — un admin l'examinera.",
        "candidature_title": "📋 Nouvelle candidature",
        "candidature_role": "Rôle demandé",
        "candidature_kingdom": "Royaume",
        "candidature_queue": "Statut",
        "candidature_insight": "AoE II Insight",
        "candidature_game_id": "ID de jeu",
        "decided": "La candidature a été marquée {}.",
        "approved": "✅ validée",
        "pending": "⏳ en attente",
        "refused": "❌ refusée",
        "role_assigned": "Le rôle {} a été attribué.",
        "no_service": "Le service d'inscription n'est pas disponible pour le moment.",
        "enroll_failed": "L'inscription a échoué ({}).",
        "enroll_queued": "Le joueur est maintenant en attente d'un royaume.",
        "enrolled_kingdom": "Inscrit dans le royaume {}.",
        "settings_title": "⚙️ Kingdoms — paramètres",
        "welcome_title": "🏰 Bienvenue dans Kingdoms — Saison II !",
        "welcome_body": (
            "Votre inscription a été validée. Lisez les règles, présentez-vous dans Présentation,"
            " et bonne chance sur le champ de bataille !"
        ),
        "welcome_fallback": "(Envoyé ici car vos messages privés sont fermés.)",
        "season_status_title": "📊 Kingdoms — Statut de la Saison II",
        "season_status_kingdoms": "👑 Royaumes",
        "season_status_queue": "⏳ Joueurs en attente",
        "season_status_empty": "aucun pour le moment",
        "season_status_hint": "Ce message est mis à jour automatiquement par le bot.",
    },
}


def _strings(locale: str) -> dict[str, str]:
    return STRINGS["fr"] if str(locale).lower().startswith("fr") else STRINGS["en"]


def _is_admin(interaction: discord.Interaction, bot_admins: tuple[str, ...]) -> bool:
    """Whether the invoking member is a BOT_ADMINS operator or guild admin."""
    user_id = getattr(interaction.user, "id", None)
    if user_id is not None and str(user_id) in bot_admins:
        return True
    permissions = getattr(interaction.user, "guild_permissions", None)
    return bool(permissions and permissions.administrator)


class _ApplicationContext:
    """Everything the ephemeral enrollment flow needs to carry along."""

    def __init__(
        self,
        locale: str = "en",
        candidatures_channel: discord.abc.Messageable | None = None,
        bot_admins: tuple[str, ...] = (),
        mod_roles_service: ModRolesService | None = None,
        guild_id: str = "",
        kingdoms_service: Any = None,
    ) -> None:
        self.locale = "fr" if str(locale).lower().startswith("fr") else "en"
        self.candidatures_channel = candidatures_channel
        self.bot_admins = bot_admins
        self.mod_roles_service = mod_roles_service
        self.guild_id = guild_id
        self.kingdoms_service = kingdoms_service

    async def declared_kingdom_names(self) -> list[str]:
        """Return the player kingdom names of the current season (empty if none)."""
        if self.kingdoms_service is None:
            return []
        try:
            kingdoms = await self.kingdoms_service.kingdoms()
        except Exception:
            logger.warning("KINGDOM PANELS: kingdom list read failed", exc_info=True)
            return []
        return sorted(k.name for k in kingdoms if getattr(k, "type", "player") != "gaia")


class _KingApplicationModal(discord.ui.Modal):
    """The King form: kingdom name + Insight link + game ID."""

    kingdom_name: discord.ui.TextInput[_KingApplicationModal] = discord.ui.TextInput(
        label="Kingdom name",
        placeholder="My Kingdom",
        max_length=40,
        required=True,
    )
    insight_link: discord.ui.TextInput[_KingApplicationModal] = discord.ui.TextInput(
        label="AoE II Insight link",
        placeholder="https://www.aoe2insight.com/…",
        max_length=200,
        required=True,
    )
    game_id: discord.ui.TextInput[_KingApplicationModal] = discord.ui.TextInput(
        label="Game ID",
        placeholder="12345678",
        max_length=20,
        required=True,
    )
    smurfs: discord.ui.TextInput[_KingApplicationModal] = discord.ui.TextInput(
        label="Smurf accounts",
        style=discord.TextStyle.paragraph,
        placeholder="https://www.aoe2insight.com/… (one per line, optional)",
        max_length=400,
        required=False,
    )

    def __init__(self, context: _ApplicationContext) -> None:
        self.context = context
        strings = _strings(context.locale)
        self.kingdom_name.label = strings["kingdom_field"][:45]
        self.kingdom_name.placeholder = strings["kingdom_placeholder"][:100]
        self.insight_link.label = strings["insight_field"][:45]
        self.insight_link.placeholder = strings["insight_placeholder"][:100]
        self.game_id.label = strings["game_id_field"][:45]
        self.game_id.placeholder = strings["game_id_placeholder"][:100]
        self.smurfs.label = strings["smurfs_field"][:45]
        self.smurfs.placeholder = strings["smurfs_placeholder"][:100]
        super().__init__(title=strings["apply_title"][:45], timeout=300)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _send_summary(
            interaction,
            self.context,
            is_king=True,
            kingdom_name=(self.kingdom_name.value or "").strip(),
            queued=False,
            insight=(self.insight_link.value or "").strip(),
            game_id=(self.game_id.value or "").strip(),
            smurfs=_parse_smurfs(self.smurfs.value or ""),
        )


class _LordApplicationModal(discord.ui.Modal):
    """The Lord form (kingdom chosen beforehand): Insight link + game ID."""

    insight_link: discord.ui.TextInput[_LordApplicationModal] = discord.ui.TextInput(
        label="AoE II Insight link",
        placeholder="https://www.aoe2insight.com/…",
        max_length=200,
        required=True,
    )
    game_id: discord.ui.TextInput[_LordApplicationModal] = discord.ui.TextInput(
        label="Game ID",
        placeholder="12345678",
        max_length=20,
        required=True,
    )
    smurfs: discord.ui.TextInput[_LordApplicationModal] = discord.ui.TextInput(
        label="Smurf accounts",
        style=discord.TextStyle.paragraph,
        placeholder="https://www.aoe2insight.com/… (one per line, optional)",
        max_length=400,
        required=False,
    )

    def __init__(self, context: _ApplicationContext, kingdom_choice: str = "") -> None:
        self.context = context
        self.kingdom_choice = kingdom_choice
        strings = _strings(context.locale)
        self.insight_link.label = strings["insight_field"][:45]
        self.insight_link.placeholder = strings["insight_placeholder"][:100]
        self.game_id.label = strings["game_id_field"][:45]
        self.game_id.placeholder = strings["game_id_placeholder"][:100]
        self.smurfs.label = strings["smurfs_field"][:45]
        self.smurfs.placeholder = strings["smurfs_placeholder"][:100]
        super().__init__(title=strings["apply_title"][:45], timeout=300)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _send_summary(
            interaction,
            self.context,
            is_king=False,
            kingdom_name=self.kingdom_choice,
            queued=self.kingdom_choice == QUEUE_VALUE,
            insight=(self.insight_link.value or "").strip(),
            game_id=(self.game_id.value or "").strip(),
            smurfs=_parse_smurfs(self.smurfs.value or ""),
        )


def _parse_smurfs(raw: str) -> tuple[str, ...]:
    """Split the smurf textarea into clean, de-duplicated links."""
    seen: dict[str, None] = {}
    for line in raw.splitlines():
        value = line.strip()
        if value:
            seen.setdefault(value, None)
    return tuple(seen)


async def _send_summary(
    interaction: discord.Interaction,
    context: _ApplicationContext,
    *,
    is_king: bool,
    kingdom_name: str,
    queued: bool,
    insight: str,
    game_id: str,
    smurfs: tuple[str, ...] = (),
) -> None:
    """Validate the form, then show the review + rules-acceptance step."""
    strings = _strings(context.locale)
    if is_king and not kingdom_name:
        await interaction.response.send_message(strings["king_needs_name"], ephemeral=True)
        return
    if not INSIGHT_URL.match(insight):
        await interaction.response.send_message(strings["bad_insight"], ephemeral=True)
        return
    if not GAME_ID.match(game_id):
        await interaction.response.send_message(strings["bad_game_id"], ephemeral=True)
        return

    role_label = strings["role_king"] if is_king else strings["role_lord"]
    lines = [f"# {strings['summary_title']}", f"**{strings['summary_role']}** : {role_label}"]
    if is_king:
        lines.append(f"**{strings['summary_kingdom']}** : {kingdom_name}")
    elif queued:
        lines.append(f"**{strings['summary_queue']}** : {strings['queue_value']}")
    else:
        lines.append(f"**{strings['summary_kingdom']}** : {kingdom_name}")
    lines.extend(
        [
            f"**{strings['summary_insight']}** : {insight}",
            f"**{strings['summary_game_id']}** : {game_id}",
        ]
    )
    if smurfs:
        lines.append(f"**{strings['summary_smurfs']}** : {', '.join(smurfs)}")
    lines.append(f"**{strings['rules_label']}**")
    view = _summary_view(
        interaction.user.id,
        interaction.user.name,
        is_king=is_king,
        kingdom_name=kingdom_name,
        queued=queued,
        insight=insight,
        game_id=game_id,
        smurfs=smurfs,
        context=context,
    )
    await interaction.response.send_message("\n".join(lines), view=view, ephemeral=True)


def _summary_view(
    applicant_id: int,
    applicant_name: str,
    *,
    is_king: bool,
    kingdom_name: str,
    queued: bool,
    insight: str,
    game_id: str,
    smurfs: tuple[str, ...],
    context: _ApplicationContext,
) -> discord.ui.View:
    """Build the review step: the rules checkbox (a button) + cancel."""
    strings = _strings(context.locale)

    async def submit(interaction: discord.Interaction) -> None:
        role_label = strings["role_king"] if is_king else strings["role_lord"]
        lines = [
            f"# 📋 {applicant_name}",
            f"**{strings['candidature_role']}** : {role_label}",
        ]
        if is_king or not queued:
            lines.append(f"**{strings['candidature_kingdom']}** : {kingdom_name}")
        else:
            lines.append(f"**{strings['candidature_queue']}** : {strings['queue_value']}")
        lines.extend(
            [
                f"**{strings['candidature_insight']}** : {insight}",
                f"**{strings['candidature_game_id']}** : {game_id}",
            ]
        )
        if smurfs:
            lines.append(f"**{strings['summary_smurfs']}** : {', '.join(smurfs)}")
        lines.append(f"<@{applicant_id}>")
        view = _candidature_view(
            context.locale,
            bot_admins=context.bot_admins,
            mod_roles_service=context.mod_roles_service,
            guild_id=context.guild_id,
        )
        target = context.candidatures_channel
        await interaction.response.defer(ephemeral=True)
        if target is None:
            await interaction.followup.send(strings["no_service"], ephemeral=True)
            return
        await target.send("\n".join(lines), view=view)
        guild = interaction.guild
        profile_note = ""
        if guild is not None:
            try:
                from kingdoms.discord.kingdom_profiles import (
                    build_profile_message,
                    ensure_profile_channel,
                )

                profile_channel = await ensure_profile_channel(guild, interaction.user)  # type: ignore[arg-type]
                if profile_channel is not None:
                    role_label = strings["role_king"] if is_king else strings["role_lord"]
                    content, profile_view = build_profile_message(
                        context.locale,
                        member_name=interaction.user.name or "joueur",
                        state=strings["pending"],
                        role_label=role_label,
                        kingdom=kingdom_name,
                        queued=queued,
                        insight=insight,
                        game_id=game_id,
                        smurfs=smurfs,
                    )
                    await profile_channel.send(content, view=profile_view)
                    profile_note = strings["sent"]
            except Exception:
                logger.warning("KINGDOM PANELS: profile provisioning failed", exc_info=True)
        await interaction.followup.send(
            f"{strings['sent']}\n{profile_note}" if profile_note else strings["sent"],
            ephemeral=True,
        )

    async def cancel(interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(content=strings["cancelled"], view=None)

    submit_button: discord.ui.Button[Any] = discord.ui.Button(
        label=strings["submit"][:80],
        style=discord.ButtonStyle.success,
        custom_id="kingdoms:apply:submit",
    )
    submit_button.callback = submit  # type: ignore[method-assign]
    cancel_button: discord.ui.Button[Any] = discord.ui.Button(
        label=strings["cancel"][:80],
        style=discord.ButtonStyle.secondary,
        custom_id="kingdoms:apply:cancel",
    )
    cancel_button.callback = cancel  # type: ignore[method-assign]
    view = discord.ui.View(timeout=600)
    view.add_item(submit_button)
    view.add_item(cancel_button)
    return view


def _role_select_view(context: _ApplicationContext) -> discord.ui.View:
    """Step 1: the role select (Seigneur / Roi — no free text)."""
    strings = _strings(context.locale)

    async def on_choose(interaction: discord.Interaction) -> None:
        values = getattr(interaction, "data", None) or {}
        chosen = [str(v) for v in values.get("values", [])]
        if not chosen:
            return
        if chosen[0] == "king":
            await interaction.response.send_modal(_KingApplicationModal(context))
            return
        await interaction.response.edit_message(
            content=f"**{strings['choose_kingdom']}**\n{strings['no_kingdoms_hint']}"
            if not await context.declared_kingdom_names()
            else f"**{strings['choose_kingdom']}**",
            view=await _kingdom_select_view(context),
        )

    select: discord.ui.Select[Any] = discord.ui.Select(
        custom_id=ROLE_SELECT_ID,
        placeholder=strings["choose_role"],
        options=[
            discord.SelectOption(label=strings["role_lord"], value="lord"),
            discord.SelectOption(label=strings["role_king"], value="king"),
        ],
    )
    select.callback = on_choose  # type: ignore[method-assign]
    view = discord.ui.View(timeout=600)
    view.add_item(select)
    return view


async def _kingdom_select_view(context: _ApplicationContext) -> discord.ui.View:
    """Step 2 (Seigneur): the declared kingdoms + the wait option."""
    strings = _strings(context.locale)
    names = await context.declared_kingdom_names()
    options = [discord.SelectOption(label=name[:100], value=name[:100]) for name in names[:24]]
    options.append(
        discord.SelectOption(
            label=strings["queue_option"],
            value=QUEUE_VALUE,
            description=strings["queue_description"][:100],
        )
    )

    async def on_choose(interaction: discord.Interaction) -> None:
        values = getattr(interaction, "data", None) or {}
        chosen = [str(v) for v in values.get("values", [])]
        if not chosen:
            return
        await interaction.response.send_modal(_LordApplicationModal(context, chosen[0]))

    select: discord.ui.Select[Any] = discord.ui.Select(
        custom_id=KINGDOM_SELECT_ID,
        placeholder=strings["choose_kingdom"],
        options=options,
    )
    select.callback = on_choose  # type: ignore[method-assign]
    view = discord.ui.View(timeout=600)
    view.add_item(select)
    return view


def _candidature_view(
    locale: str,
    bot_admins: tuple[str, ...],
    mod_roles_service: ModRolesService | None = None,
    guild_id: str = "",
) -> discord.ui.View:
    """Build the admin-only decision row of one candidature message.

    Approving assigns the kingdoms_lord or kingdoms_king mod role to
    the applicant through the ModRolesService — never a raw Discord
    role id. The role key rides the application message: the decision
    row is rebuilt from the wire (restart-proof by re-deploy).
    """
    strings = _strings(locale)

    async def decide(interaction: discord.Interaction, decision: str) -> None:
        if not _is_admin(interaction, bot_admins):
            await interaction.response.send_message("Only admins can decide on applications.", ephemeral=True)
            return
        label = {
            "approve": strings["approved"],
            "pending": strings["pending"],
            "refuse": strings["refused"],
        }[decision]
        content = interaction.message.content if interaction.message is not None else ""
        await interaction.response.edit_message(content=content, view=None)
        note = strings["decided"].format(label)
        if decision == "approve":
            applicant = re.search(r"<@(\d+)>", content)
            is_king = strings["role_king"] in content
            role_key = ROLE_KING if is_king else ROLE_LORD
            if mod_roles_service is not None and applicant is not None:
                try:
                    await mod_roles_service.assign_mod_role(guild_id, applicant.group(1), KINGDOM_MOD, role_key)
                    note += " " + strings["role_assigned"].format(role_key)
                except Exception:
                    logger.warning("CANDIDATURES: role assignment failed", exc_info=True)
                    note += " " + strings["no_service"]
        await interaction.followup.send(note, ephemeral=True)

    from kingdoms.discord.kingdom_persistent import KingdomCandidatureButton

    view = discord.ui.View(timeout=None)
    view.add_item(KingdomCandidatureButton("approve", "✅", discord.ButtonStyle.success))
    view.add_item(KingdomCandidatureButton("pending", "⏳", discord.ButtonStyle.secondary))
    view.add_item(KingdomCandidatureButton("refuse", "❌", discord.ButtonStyle.danger))
    return view


# Designer-authored Postuler header (kingdoms#138 group 1): golden
# accent (the King colour), artwork thumbnail when the asset is
# bundled, one tagline — everything else lives in the flow itself.
APPLY_TITLE = "⚔️ INSCRIPTION — KINGDOMS SAISON II"
APPLY_TAGLINE = "Choisis ton rôle et pars à la conquête des territoires !"
GOLD = discord.Colour(0xE6B800)


def _apply_artwork_path() -> Path:
    """Return the bundled Postuler artwork path (may not exist).

    Computed lazily instead of as a module constant: pydoc renders module
    data values verbatim, and an absolute path here would make the generated
    docs depend on the checkout directory (pydoc freshness gate).
    """
    return Path(__file__).resolve().parent.parent / "assets" / "kingdoms_artwork.png"


async def build_apply_panel(
    locale: str = "en",
    candidatures_channel: discord.abc.Messageable | None = None,
    bot_admins: tuple[str, ...] = (),
    mod_roles_service: ModRolesService | None = None,
    guild_id: str = "",
    kingdoms_service: Any = None,
) -> discord.ui.LayoutView:
    """Build the Postuler pinned panel: one Enroll button."""
    strings = _strings(locale)
    del (
        candidatures_channel,
        bot_admins,
        mod_roles_service,
        guild_id,
        kingdoms_service,
    )

    from kingdoms.discord.kingdom_persistent import KingdomApplyButton

    apply_button = KingdomApplyButton(strings["apply_button"][:80], discord.ButtonStyle.primary)
    apply_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    apply_row.add_item(apply_button)
    view = discord.ui.LayoutView(timeout=None)
    items: list[discord.ui.Item[discord.ui.LayoutView]] = [
        discord.ui.TextDisplay(f"# {APPLY_TITLE}"),
        discord.ui.Separator(),
    ]
    if _apply_artwork_path().is_file():
        items.append(
            discord.ui.Section(
                discord.ui.TextDisplay(APPLY_TAGLINE),
                accessory=discord.ui.Thumbnail(media="attachment://kingdoms_artwork.png"),
            )
        )
    else:
        items.append(discord.ui.TextDisplay(APPLY_TAGLINE))
    items.extend(
        [
            discord.ui.Separator(),
            apply_row,
            discord.ui.Separator(),
            discord.ui.TextDisplay(f"-# {PANEL_MARKER}"),
        ]
    )
    view.add_item(
        discord.ui.Container(
            *items,
            accent_colour=GOLD,
        )
    )
    return view


async def build_settings_panel(
    logs_service: LogService | None,
    guild_id: str,
    by: str,
    bot_admins: tuple[str, ...] = (),
    roles_service: Any = None,
) -> discord.ui.LayoutView:
    """Build the Paramètres panel: the admin season actions.

    Language and timezone live in the platform core (bot-admin) — the mod
    only exposes its own season actions here.
    """
    locale = "en"
    if logs_service is not None:
        try:
            locale = await logs_service.get_locale(guild_id)
        except Exception:
            logger.warning("KINGDOM SETTINGS: locale read failed", exc_info=True)
    strings = _strings(locale)

    from kingdoms.discord.kingdom_persistent import KingdomAdminButton
    from kingdoms.discord.kingdom_profiles import _strings as profile_strings

    admin_strings = profile_strings(locale)
    buttons = (
        ("launch", "launch_button", discord.ButtonStyle.success),
        ("status", "status_button", discord.ButtonStyle.secondary),
        ("assign", "assign_button", discord.ButtonStyle.primary),
        ("add-kingdom", "add_kingdom_button", discord.ButtonStyle.primary),
        ("remove", "remove_player_button", discord.ButtonStyle.danger),
        ("replace", "replace_button", discord.ButtonStyle.primary),
        ("name", "name_button", discord.ButtonStyle.secondary),
        ("deploy", "deploy_button", discord.ButtonStyle.primary),
        ("sync", "sync_button", discord.ButtonStyle.secondary),
        ("reset-data", "reset_data_button", discord.ButtonStyle.danger),
        ("reset-full", "reset_full_button", discord.ButtonStyle.danger),
        ("reset", "reset_salons_button", discord.ButtonStyle.danger),
    )
    season_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    roster_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    maintenance_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    for index, (action, key, style) in enumerate(buttons):
        target = season_row if index < 5 else roster_row if index < 7 else maintenance_row
        target.add_item(KingdomAdminButton(action, admin_strings[key][:80], style))

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay(f"# {strings['settings_title']}"),
            discord.ui.Separator(),
            discord.ui.TextDisplay(f"## 🛠️ {admin_strings['admin_section']}"),
            season_row,
            roster_row,
            maintenance_row,
            discord.ui.Separator(),
            discord.ui.TextDisplay(f"-# {SETTINGS_PANEL_MARKER}"),
            accent_colour=GREEN,
        )
    )
    return view


async def refresh_season_status(
    guild: discord.Guild,
    locale: str,
    kingdoms: tuple[str, ...] | list[str] = (),
    queued: int = 0,
    progress: str = "",
) -> bool:
    """Post (or refresh) the season status message in the dedicated channel."""
    strings = _strings(locale)
    from kingdoms.discord.kingdom_setup import _slug

    channel = next(
        (c for c in guild.text_channels if _slug(c.name) == SEASON_STATUS_CHANNEL),
        None,
    )
    if channel is None:
        return False
    names = list(kingdoms)
    lines = [
        f"# {strings['season_status_title']}",
    ]
    if progress:
        lines.append(f"**{progress}**")
    names_line = ", ".join(names) if names else strings["season_status_empty"]
    lines.extend(
        [
            f"**{strings['season_status_kingdoms']}** : {names_line}",
            f"**{strings['season_status_queue']}** : {queued if queued else strings['season_status_empty']}",
            f"*{strings['season_status_hint']}*",
            SEASON_STATUS_MARKER,
        ]
    )
    content = "\n".join(lines)
    for message in list(getattr(channel, "messages", [])):
        if SEASON_STATUS_MARKER in (message.content or ""):
            try:
                await message.edit(content=content)
            except Exception:
                logger.warning("KINGDOM PANELS: season status refresh failed", exc_info=True)
            return True
    await channel.send(content)
    return True



async def _deploy_settings_panel(
    channel: discord.TextChannel,
    logs_service: LogService | None,
    guild_id: str,
    bot_admins: tuple[str, ...],
) -> None:
    """Remove every stale settings message (old locale/timezone panel included), then pin the fresh one."""
    for message in list(getattr(channel, "messages", [])):
        stale = (
            SETTINGS_PANEL_MARKER in (message.content or "")
            or "kingdoms:settings:" in (message.content or "")
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
                logger.warning("KINGDOM PANELS: old settings panel removal failed", exc_info=True)
    await channel.send(view=await build_settings_panel(logs_service, guild_id, "system", bot_admins))


async def _deploy_apply_panel(
    channel: discord.TextChannel,
    candidatures: discord.TextChannel | None,
    locale: str,
    bot_admins: tuple[str, ...],
    mod_roles_service: ModRolesService | None,
    guild_id: str,
    kingdoms_service: Any,
) -> None:
    """Remove the old apply panel then pin a fresh one."""
    for message in list(getattr(channel, "messages", [])):
        if PANEL_MARKER in (message.content or ""):
            try:
                await message.delete()
            except Exception:
                logger.warning("KINGDOM PANELS: old apply panel removal failed", exc_info=True)
    kwargs: dict[str, Any] = {}
    if _apply_artwork_path().is_file():
        kwargs["file"] = discord.File(_apply_artwork_path(), filename="kingdoms_artwork.png")
    await channel.send(
        view=await build_apply_panel(
            locale,
            candidatures,
            bot_admins,
            mod_roles_service,
            guild_id,
            kingdoms_service,
        ),
        **kwargs,
    )


async def deploy_panels(
    guild: discord.Guild,
    logs_service: LogService | None,
    bot_admins: tuple[str, ...] = (),
    mod_roles_service: ModRolesService | None = None,
    kingdoms_service: Any = None,
) -> dict[str, str]:
    """Deploy the pinned panels into Postuler and Paramètres.

    Returns a report dict {panel: status}. Idempotent in v1: each
    deployment pins a fresh panel message (existing pins from previous
    versions are not removed — a later slice will adopt them).
    """
    report: dict[str, str] = {}
    locale = "en"
    guild_id = str(guild.id)
    if logs_service is not None:
        try:
            locale = await logs_service.get_locale(guild_id)
        except Exception:
            logger.warning("KINGDOM PANELS: locale read failed", exc_info=True)
    from kingdoms.discord.kingdom_setup import _slug

    candidatures = next((c for c in guild.text_channels if _slug(c.name) == "candidatures"), None)
    for channel in guild.text_channels:
        if _slug(channel.name) == "postuler":
            await _deploy_apply_panel(
                channel, candidatures, locale, bot_admins, mod_roles_service, guild_id, kingdoms_service
            )
            report["postuler"] = "deployed"
        if _slug(channel.name) == "parametres":
            await _deploy_settings_panel(channel, logs_service, guild_id, bot_admins)
            report["paramètres"] = "deployed"
        if _slug(channel.name) == "marche":
            from kingdoms.discord.kingdom_market import deploy_market_panel

            await deploy_market_panel(channel, kingdoms_service, locale)
            report["marché"] = "deployed"
    context = _ApplicationContext(
        locale=locale,
        bot_admins=bot_admins,
        mod_roles_service=mod_roles_service,
        guild_id=guild_id,
        kingdoms_service=kingdoms_service,
    )
    try:
        if await refresh_season_status(
            guild,
            locale,
            kingdoms=await context.declared_kingdom_names(),
            progress=await _season_progress(kingdoms_service, locale),
        ):
            report["statut saison"] = "deployed"
    except Exception:
        logger.warning("KINGDOM PANELS: season status deployment failed", exc_info=True)
    try:
        if await refresh_changelog(guild, locale):
            report["update"] = "deployed"
    except Exception:
        logger.warning("KINGDOM PANELS: changelog deployment failed", exc_info=True)
    from kingdoms.discord.kingdom_content import refresh_salons_content

    report.update(await refresh_salons_content(guild, locale, kingdoms_service))
    return report


async def _season_progress(kingdoms_service: Any, locale: str) -> str:
    """Compute the cycle/age progress line (empty when no season runs)."""
    if kingdoms_service is None:
        return ""
    try:
        season = await kingdoms_service.current_season()
        if season is None:
            return ""
        return season_label(season, kingdoms_service.config, locale=locale)
    except Exception:
        logger.warning("KINGDOM PANELS: season progress read failed", exc_info=True)
        return ""


def register_kingdom_panels_command(
    tree: app_commands.CommandTree[discord.Client],
    logs_service: LogService | None = None,
    bot_admins: tuple[str, ...] = (),
    mod_roles_service: ModRolesService | None = None,
    kingdoms_service: Any = None,
) -> None:
    """Register the /kingdom command: bootstrap + panels, admin only."""

    @tree.command(
        name=localized("commands.kingdom_name", "kingdom"),
        description=localized(
            "commands.kingdom_description",
            "Bootstrap the Kingdoms salons and panels (admin only)",
        ),
    )
    @app_commands.default_permissions(administrator=True)
    async def kingdom_command(interaction: discord.Interaction) -> None:
        """Provision the structure, deploy the panels, report both."""
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("The /kingdom command must run inside a server.", ephemeral=True)
            return
        if not _is_admin(interaction, bot_admins):
            await interaction.response.send_message(
                "You are not allowed to bootstrap Kingdoms (admins only).",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            from kingdoms.discord.kingdom_setup import provision_structure

            created, adopted = await provision_structure(guild)
            panels = await deploy_panels(guild, logs_service, bot_admins, mod_roles_service, kingdoms_service)
        except Exception as exc:
            logger.exception("KINGDOM SETUP: provisioning failed for guild %s", guild.id)
            await interaction.followup.send(f"❌ Setup failed: `{type(exc).__name__}: {exc}`"[:2000], ephemeral=True)
            return
        from kingdoms.discord.kingdom_setup import build_setup_report_view

        locale = str(interaction.locale) if interaction.locale else "en"
        view = build_setup_report_view(created, adopted, locale)
        extra = "\n".join(f"{name}: {status}" for name, status in panels.items())
        if extra:
            await interaction.followup.send(content=extra, ephemeral=True)
            await interaction.followup.send(view=view, ephemeral=True)
        else:
            await interaction.followup.send(view=view, ephemeral=True)
