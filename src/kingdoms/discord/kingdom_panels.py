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
- **Paramètres** (admin only) — the guild language (fr/en) and the
  reference timezone, persisted in the guild settings (the timezone is
  the designer's common time reference for delays and events).

The mod declares its roles in ``config/mods/kingdoms.yaml``
(kingdoms_king, kingdoms_lord); the panels assign them through the
ModRolesService — never raw Discord role IDs. Panels degrade when the
stores are not wired (local runs): the buttons answer with a note.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import discord
from discord import app_commands

from kingdoms.core.services.logs import LogService
from kingdoms.core.services.roles import ModRolesService
from kingdoms.discord.commands_i18n import localized
from kingdoms.discord.ui import (
    BLURPLE,
    GREEN,
    Action,
    Container,
    Option,
    Row,
    SelectMenu,
    Separator,
    Text,
    UILayout,
)

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
LOCALE_SELECT_ID = "kingdoms:settings:locale"
TIMEZONE_SELECT_ID = "kingdoms:settings:timezone"

QUEUE_VALUE = "__queue__"

SUPPORTED_LOCALES = ("en", "fr")
SUPPORTED_TIMEZONES = ("Europe/Paris", "America/Montreal", "America/Sao_Paulo", "UTC")

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
        "summary_title": "📋 Review your application",
        "summary_role": "Role",
        "summary_kingdom": "Kingdom",
        "summary_queue": "Status",
        "queue_value": "waiting for a kingdom",
        "summary_insight": "AoE II Insight",
        "summary_game_id": "Game ID",
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
        "settings_title": "⚙️ Kingdoms — settings",
        "language": "Language",
        "timezone": "Reference timezone",
        "language_hint": "Applied to every kingdoms message in this server.",
        "timezone_hint": "Every displayed time (delays, events) uses this reference.",
        "saved": "Saved.",
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
        "summary_title": "📋 Vérifiez votre candidature",
        "summary_role": "Rôle",
        "summary_kingdom": "Royaume",
        "summary_queue": "Statut",
        "queue_value": "en attente d'un royaume",
        "summary_insight": "AoE II Insight",
        "summary_game_id": "ID de jeu",
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
        "settings_title": "⚙️ Kingdoms — paramètres",
        "language": "Langue",
        "timezone": "Fuseau horaire de référence",
        "language_hint": "Appliquée à tous les messages kingdoms de ce serveur.",
        "timezone_hint": "Toutes les heures affichées (délais, événements) suivent cette référence.",
        "saved": "Enregistré.",
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

    def __init__(self, context: _ApplicationContext) -> None:
        self.context = context
        strings = _strings(context.locale)
        self.kingdom_name.label = strings["kingdom_field"][:45]
        self.kingdom_name.placeholder = strings["kingdom_placeholder"][:100]
        self.insight_link.label = strings["insight_field"][:45]
        self.insight_link.placeholder = strings["insight_placeholder"][:100]
        self.game_id.label = strings["game_id_field"][:45]
        self.game_id.placeholder = strings["game_id_placeholder"][:100]
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

    def __init__(self, context: _ApplicationContext, kingdom_choice: str = "") -> None:
        self.context = context
        self.kingdom_choice = kingdom_choice
        strings = _strings(context.locale)
        self.insight_link.label = strings["insight_field"][:45]
        self.insight_link.placeholder = strings["insight_placeholder"][:100]
        self.game_id.label = strings["game_id_field"][:45]
        self.game_id.placeholder = strings["game_id_placeholder"][:100]
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
        )


async def _send_summary(
    interaction: discord.Interaction,
    context: _ApplicationContext,
    *,
    is_king: bool,
    kingdom_name: str,
    queued: bool,
    insight: str,
    game_id: str,
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
            f"**{strings['rules_label']}**",
        ]
    )
    view = _summary_view(
        interaction.user.id,
        is_king=is_king,
        kingdom_name=kingdom_name,
        queued=queued,
        insight=insight,
        game_id=game_id,
        context=context,
    )
    await interaction.response.send_message("\n".join(lines), view=view, ephemeral=True)


def _summary_view(
    applicant_id: int,
    *,
    is_king: bool,
    kingdom_name: str,
    queued: bool,
    insight: str,
    game_id: str,
    context: _ApplicationContext,
) -> discord.ui.View:
    """Build the review step: the rules checkbox (a button) + cancel."""
    strings = _strings(context.locale)

    async def submit(interaction: discord.Interaction) -> None:
        role_label = strings["role_king"] if is_king else strings["role_lord"]
        lines = [
            f"# {strings['candidature_title']}",
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
                f"<@{applicant_id}>",
            ]
        )
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
        await interaction.followup.send(strings["sent"], ephemeral=True)

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

    class _DecisionButton(discord.ui.Button["discord.ui.View"]):
        def __init__(self, decision: str, emoji: str, style: discord.ButtonStyle) -> None:
            super().__init__(emoji=emoji, style=style)
            self._decision = decision

        async def callback(self, interaction: discord.Interaction) -> None:
            await decide(interaction, self._decision)

    view = discord.ui.View(timeout=None)
    view.add_item(_DecisionButton("approve", "✅", discord.ButtonStyle.success))
    view.add_item(_DecisionButton("pending", "⏳", discord.ButtonStyle.secondary))
    view.add_item(_DecisionButton("refuse", "❌", discord.ButtonStyle.danger))
    return view


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
    context = _ApplicationContext(
        locale=locale,
        candidatures_channel=candidatures_channel,
        bot_admins=bot_admins,
        mod_roles_service=mod_roles_service,
        guild_id=guild_id,
        kingdoms_service=kingdoms_service,
    )

    async def on_apply(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            f"**{_strings(context.locale)['choose_role']}**",
            view=_role_select_view(context),
            ephemeral=True,
        )

    apply_button = Action(strings["apply_button"], APPLY_BUTTON_ID, on_apply, style="primary")
    container = (
        Container(accent=BLURPLE)
        .add(Text(f"# 📝 {'Inscriptions' if locale.startswith('fr') else 'Enrollment'}"))
        .add(Separator())
        .add(Row(apply_button))
    )
    return UILayout().add(container).build()


async def _write_guild_setting(
    logs_service: LogService | None,
    guild_id: str,
    by: str,
    value: str,
) -> None:
    """Persist one guild setting: a locale or the reference timezone."""
    if logs_service is None:
        return
    import time as _time

    try:
        if value in SUPPORTED_LOCALES:
            await logs_service.set_locale(guild_id, value, by=by)
            return
        settings = await logs_service._safe(logs_service._db.get_guild_settings(guild_id)) or {}
        settings["timezone"] = value
        settings["updated_at"] = int(_time.time())
        settings["updated_by"] = by
        await logs_service._db.set_guild_settings(guild_id, settings)
    except Exception:
        logger.warning("KINGDOM SETTINGS: guild setting write failed", exc_info=True)


async def build_settings_panel(
    logs_service: LogService | None,
    guild_id: str,
    by: str,
    bot_admins: tuple[str, ...] = (),
    roles_service: Any = None,
) -> discord.ui.LayoutView:
    """Build the Paramètres panel: guild language + reference timezone."""
    locale = "en"
    if logs_service is not None:
        try:
            locale = await logs_service.get_locale(guild_id)
        except Exception:
            logger.warning("KINGDOM SETTINGS: locale read failed", exc_info=True)
    strings = _strings(locale)

    async def apply_and_rerender(interaction: discord.Interaction, values: list[str]) -> None:
        if not values or not _is_admin(interaction, bot_admins):
            return
        await _write_guild_setting(logs_service, guild_id, by, values[0])
        await interaction.response.edit_message(view=await build_settings_panel(logs_service, guild_id, by, bot_admins))

    async def on_locale(interaction: discord.Interaction, values: list[str]) -> None:
        await apply_and_rerender(interaction, values)

    async def on_timezone(interaction: discord.Interaction, values: list[str]) -> None:
        await apply_and_rerender(interaction, values)

    locale_select = SelectMenu(
        custom_id=LOCALE_SELECT_ID,
        options=tuple(Option(label, label) for label in SUPPORTED_LOCALES),
        on_choose=on_locale,
        placeholder=strings["language"],
    )
    timezone_select = SelectMenu(
        custom_id=TIMEZONE_SELECT_ID,
        options=tuple(Option(label, label) for label in SUPPORTED_TIMEZONES),
        on_choose=on_timezone,
        placeholder=strings["timezone"],
    )
    container = (
        Container(accent=GREEN)
        .add(Text(f"# {strings['settings_title']}"))
        .add(Separator())
        .add(Text(f"## 🌐 {strings['language']}"))
        .add(Text(strings["language_hint"]))
        .add(Row(locale_select))
        .add(Separator())
        .add(Text(f"## 🕒 {strings['timezone']}"))
        .add(Text(strings["timezone_hint"]))
        .add(Row(timezone_select))
    )
    return UILayout().add(container).build()


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
    candidatures = next((c for c in guild.text_channels if c.name.lower() == "candidatures"), None)
    for channel in guild.text_channels:
        if channel.name.lower() == "postuler":
            await channel.send(
                view=await build_apply_panel(
                    locale,
                    candidatures,
                    bot_admins,
                    mod_roles_service,
                    guild_id,
                    kingdoms_service,
                )
            )
            report["postuler"] = "deployed"
        if channel.name.lower() == "paramètres":
            await channel.send(view=await build_settings_panel(logs_service, guild_id, "system", bot_admins))
            report["paramètres"] = "deployed"
    return report


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
