"""The /kingdoms-admin command group: the T2 season management surface.

Admin-only (bot operators, guild administrators, the bot-admins role —
the same click-time guard as /admin): launch a season, reset it,
approve or refuse a proposed kingdom name, assign queued players and
replace departed ones (reference §3-§5, D21-D26/D38).

Answers are ephemeral — admin actions never flood the season channels.
The designer strings live in the FR/EN dict below; command names and
descriptions localize through the shared catalog
(``commands.kingdoms_admin_*`` keys).
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

import discord
from discord import app_commands

from kingdoms.discord.commands_i18n import localized
from kingdoms.discord.guards import require_admin
from kingdoms.mods.kingdoms.models import KingdomModel, LordRole, SeasonState
from kingdoms.mods.kingdoms.service import KingdomsModError, KingdomsService

logger = logging.getLogger("kingdoms.kingdoms_admin")

DEFAULT_LOCALE = "en"

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "group_description": "Kingdoms season management: launch, reset, names, assignments",
        "launch_name": "launch",
        "launch_description": "Launch a new season (resets the previous one)",
        "launch_names_arg": "names",
        "launch_names_arg_description": "Comma-separated kingdom names (empty = free founding)",
        "reset_name": "reset",
        "reset_description": "Reset the season data without launching",
        "status_name": "season",
        "status_description": "The current season status: cycle, kingdoms, queue",
        "name_name": "name",
        "name_description": "Approve or refuse a proposed kingdom name",
        "name_kingdom_arg": "kingdom",
        "name_kingdom_arg_description": "The kingdom whose name is decided",
        "name_decision_arg": "decision",
        "name_decision_arg_description": "Approve the proposed name or refuse it",
        "name_approve": "Approve",
        "name_refuse": "Refuse",
        "assign_name": "assign",
        "assign_description": "Assign a queued player to a kingdom",
        "assign_player_arg": "player",
        "assign_player_arg_description": "The queued player to assign",
        "assign_kingdom_arg": "kingdom",
        "assign_kingdom_arg_description": "The kingdom the player joins",
        "assign_role_arg": "role",
        "assign_role_arg_description": "King or Lord in the target kingdom",
        "replace_name": "replace",
        "replace_description": "Replace a departed player with a queued one (D23)",
        "replace_outgoing_arg": "outgoing",
        "replace_outgoing_arg_description": "The player who left",
        "replace_incoming_arg": "incoming",
        "replace_incoming_arg_description": "The queued player taking over",
        "enroll_role_king": "King",
        "enroll_role_lord": "Lord",
        "service_unavailable": "The kingdoms service is not available on this deployment.",
        "denied": "You are not allowed to do that — this action is reserved for bot admins.",
        "launched_free": "🚀 Season **{season}** launched — players may found kingdoms and enroll.",
        "launched_imposed": (
            "🚀 Season **{season}** launched with imposed kingdoms: {kingdoms} "
            "— players enroll and you assign them."
        ),
        "reset_done": "🧹 Season data reset — no season is running now.",
        "status_none": "No season is running. Launch one with `/kingdoms-admin launch`.",
        "status_line": (
            "🚀 Season **{season}** — cycle {cycle}/{weeks} — {kingdoms} kingdoms, "
            "{queue} player(s) waiting."
        ),
        "name_approved": "✅ The kingdom name **{kingdom}** is approved.",
        "name_refused": "❌ The name of **{kingdom}** was refused — it now reads `{fallback}`.",
        "assigned": "🎖️ {player} joined **{kingdom}** as {role}.",
        "replaced": (
            "🔁 {incoming} replaces {outgoing} in **{kingdom}** — "
            "weekly attack/defense state inherited."
        ),
    },
    "fr": {
        "group_description": "Gestion de la saison Kingdoms : lancement, reset, noms, affectations",
        "launch_name": "lancer",
        "launch_description": "Lancer une nouvelle saison (réinitialise la précédente)",
        "launch_names_arg": "noms",
        "launch_names_arg_description": "Noms des royaumes séparés par des virgules (vide = fondation libre)",
        "reset_name": "reset",
        "reset_description": "Réinitialiser les données de saison sans lancer",
        "status_name": "saison",
        "status_description": "L'état de la saison : cycle, royaumes, file d'attente",
        "name_name": "nom",
        "name_description": "Valider ou refuser un nom de royaume proposé",
        "name_kingdom_arg": "royaume",
        "name_kingdom_arg_description": "Le royaume dont on décide du nom",
        "name_decision_arg": "decision",
        "name_decision_arg_description": "Valider le nom proposé ou le refuser",
        "name_approve": "Valider",
        "name_refuse": "Refuser",
        "assign_name": "affecter",
        "assign_description": "Affecter un joueur en attente à un royaume",
        "assign_player_arg": "joueur",
        "assign_player_arg_description": "Le joueur en attente à affecter",
        "assign_kingdom_arg": "royaume",
        "assign_kingdom_arg_description": "Le royaume que le joueur rejoint",
        "assign_role_arg": "role",
        "assign_role_arg_description": "Roi ou Seigneur dans le royaume cible",
        "replace_name": "remplacer",
        "replace_description": "Remplacer un joueur parti par un joueur en attente (D23)",
        "replace_outgoing_arg": "sortant",
        "replace_outgoing_arg_description": "Le joueur qui a quitté",
        "replace_incoming_arg": "entrant",
        "replace_incoming_arg_description": "Le joueur en attente qui prend la place",
        "enroll_role_king": "Roi",
        "enroll_role_lord": "Seigneur",
        "service_unavailable": "Le service kingdoms n'est pas disponible sur ce déploiement.",
        "denied": "Vous n'êtes pas autorisé — cette action est réservée aux admins du bot.",
        "launched_free": "🚀 Saison **{season}** lancée — les joueurs peuvent fonder des royaumes et s'inscrire.",
        "launched_imposed": (
            "🚀 Saison **{season}** lancée avec royaumes imposés : {kingdoms} "
            "— les joueurs s'inscrivent et vous les affectez."
        ),
        "reset_done": "🧹 Données de saison réinitialisées — aucune saison en cours.",
        "status_none": "Aucune saison en cours. Lancez-en une avec `/kingdoms-admin lancer`.",
        "status_line": (
            "🚀 Saison **{season}** — cycle {cycle}/{weeks} — {kingdoms} royaumes, "
            "{queue} joueur(s) en attente."
        ),
        "name_approved": "✅ Le nom du royaume **{kingdom}** est validé.",
        "name_refused": "❌ Le nom de **{kingdom}** a été refusé — il s'appelle désormais `{fallback}`.",
        "assigned": "🎖️ {player} a rejoint **{kingdom}** comme {role}.",
        "replaced": (
            "🔁 {incoming} remplace {outgoing} dans **{kingdom}** — "
            "l'état attaque/défense de la semaine est hérité."
        ),
    },
}

ERROR_STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "no_season": "No season is running.",
        "already_enrolled": "This player is already enrolled.",
        "name_invalid": "This name breaks the configured rules.",
        "kingdom_limit": "The season already counts its maximum of kingdoms.",
        "imposed": "Kingdoms are imposed this season.",
        "kingdom_not_found": "No kingdom with this name.",
        "kingdom_full": "This kingdom already counts its maximum of Lords.",
        "not_enrollable": "Gaïa kingdoms never enroll players.",
        "not_queued": "This player is not waiting in the queue.",
        "replacement": "The outgoing player has not left the season yet.",
        "unexpected": "An unexpected error occurred. Try again.",
    },
    "fr": {
        "no_season": "Aucune saison en cours.",
        "already_enrolled": "Ce joueur est déjà inscrit.",
        "name_invalid": "Ce nom ne respecte pas les règles configurées.",
        "kingdom_limit": "La saison compte déjà son nombre maximum de royaumes.",
        "imposed": "Les royaumes sont imposés cette saison.",
        "kingdom_not_found": "Aucun royaume de ce nom.",
        "kingdom_full": "Ce royaume compte déjà son nombre maximum de Seigneurs.",
        "not_enrollable": "Les royaumes Gaïa n'inscrivent jamais de joueurs.",
        "not_queued": "Ce joueur n'est pas en file d'attente.",
        "replacement": "Le joueur sortant n'a pas encore quitté la saison.",
        "unexpected": "Une erreur inattendue est survenue. Réessayez.",
    },
}

def _locale_key(locale: discord.Locale | None) -> str:
    """Resolve an interaction locale to its string-set key (English fallback)."""
    if locale is not None and str(locale).startswith("fr"):
        return "fr"
    return DEFAULT_LOCALE


def _strings_for(locale: discord.Locale | None) -> dict[str, str]:
    """Pick the string set for an interaction locale (English fallback)."""
    return STRINGS[_locale_key(locale)]


def _error_text(locale: discord.Locale | None, error: KingdomsModError) -> str:
    """Resolve a mod error into its designer-authored message."""
    errors = ERROR_STRINGS[_locale_key(locale)]
    return errors.get(error.message_key.split(".")[-1], errors["unexpected"])


Guard = Callable[[discord.Interaction], Awaitable[bool]]


def register_kingdoms_admin_command(
    tree: app_commands.CommandTree[discord.Client],
    service: KingdomsService | None,
    bot_admins: tuple[str, ...],
    roles_service: object | None = None,
) -> None:
    """Register the /kingdoms-admin command group on the command tree."""
    group = app_commands.Group(
        name=localized("commands.kingdoms_admin_name", "kingdoms-admin"),
        description=localized(
            "commands.kingdoms_admin_description",
            STRINGS[DEFAULT_LOCALE]["group_description"],
        ),
        guild_only=True,
        default_permissions=discord.Permissions(administrator=True),
    )
    tree.add_command(group)
    guard = _make_guard(bot_admins, roles_service)
    _register_launch(group, service, guard)
    _register_reset(group, service, guard)
    _register_status(group, service, guard)
    _register_decide_name(group, service, guard)
    _register_assign(group, service, guard)
    _register_replace(group, service, guard)


def _make_guard(
    bot_admins: tuple[str, ...],
    roles_service: object | None,
) -> Guard:
    """Build the click-time admin guard shared by every subcommand."""

    async def guard(interaction: discord.Interaction) -> bool:
        """Deny non-admins at invocation time (the click-time rule)."""
        allowed = await require_admin(interaction, bot_admins, roles_service)  # type: ignore[arg-type]
        if not allowed:
            strings = _strings_for(interaction.locale)
            if interaction.response.is_done():
                await interaction.followup.send(strings["denied"], ephemeral=True)
            else:
                await interaction.response.send_message(strings["denied"], ephemeral=True)
        return allowed

    return guard


async def _check(
    interaction: discord.Interaction,
    strings: dict[str, str],
    service: KingdomsService | None,
    guard: Guard,
) -> KingdomsService | None:
    """Defer, enforce the admin guard, then narrow the service wiring.

    Returns None when the guard denied the caller or the service is not
    wired (the caller already answered the user).
    """
    await interaction.response.defer(ephemeral=True)
    if not await guard(interaction):
        return None
    if service is None:
        await interaction.followup.send(strings["service_unavailable"], ephemeral=True)
        return None
    return service


def _member_id(member: discord.Member | discord.User) -> str:
    """Read the interaction member id as the stored lord key."""
    return str(member.id)


def _register_launch(
    group: app_commands.Group,
    service: KingdomsService | None,
    guard: Guard,
) -> None:
    """Register the season launch subcommand (D38)."""

    @group.command(
        name=localized(
            "commands.kingdoms_admin_launch_name",
            STRINGS[DEFAULT_LOCALE]["launch_name"],
        ),
        description=localized(
            "commands.kingdoms_admin_launch_description",
            STRINGS[DEFAULT_LOCALE]["launch_description"],
        ),
    )
    async def launch(interaction: discord.Interaction) -> None:
        """Launch a new season: wholesale reset, then the fresh state (D38).

        A launch never creates kingdoms (Drasah's rule): kingdoms appear
        through a lord's proposal or the admin « add a kingdom » action.
        """
        strings = _strings_for(interaction.locale)
        svc = await _check(interaction, strings, service, guard)
        if svc is None:
            return
        try:
            season = await svc.launch()
        except KingdomsModError as error:
            await interaction.followup.send(_error_text(interaction.locale, error), ephemeral=True)
            return
        await _answer_launch(interaction, strings, season)


async def _answer_launch(
    interaction: discord.Interaction,
    strings: dict[str, str],
    season: SeasonState,
) -> None:
    """Answer a launch with the free-founding mode message."""
    await interaction.followup.send(
        strings["launched_free"].format(season=season.id), ephemeral=True
    )
    logger.info("kingdoms: season launched by %s", interaction.user.id)
    if interaction.guild is not None:
        from kingdoms.mods.kingdoms.kingdom_persistent import _ensure_realms_after_launch

        await _ensure_realms_after_launch(interaction.guild, str(interaction.locale))


def _register_reset(
    group: app_commands.Group,
    service: KingdomsService | None,
    guard: Guard,
) -> None:
    """Register the season reset subcommand (reference section 3.3)."""

    @group.command(
        name=localized(
            "commands.kingdoms_admin_reset_name",
            STRINGS[DEFAULT_LOCALE]["reset_name"],
        ),
        description=localized(
            "commands.kingdoms_admin_reset_description",
            STRINGS[DEFAULT_LOCALE]["reset_description"],
        ),
    )
    async def reset(interaction: discord.Interaction) -> None:
        """Reset the season data without launching anything."""
        strings = _strings_for(interaction.locale)
        svc = await _check(interaction, strings, service, guard)
        if svc is None:
            return
        await svc.reset()
        await interaction.followup.send(strings["reset_done"], ephemeral=True)
        logger.info("kingdoms: season data reset by %s", interaction.user.id)


def _register_status(
    group: app_commands.Group,
    service: KingdomsService | None,
    guard: Guard,
) -> None:
    """Register the season status subcommand."""

    @group.command(
        name=localized(
            "commands.kingdoms_admin_status_name",
            STRINGS[DEFAULT_LOCALE]["status_name"],
        ),
        description=localized(
            "commands.kingdoms_admin_status_description",
            STRINGS[DEFAULT_LOCALE]["status_description"],
        ),
    )
    async def status(interaction: discord.Interaction) -> None:
        """Summarize the running season: cycle, kingdoms, queue."""
        strings = _strings_for(interaction.locale)
        svc = await _check(interaction, strings, service, guard)
        if svc is None:
            return
        season = await svc.current_season()
        if season is None:
            await interaction.followup.send(strings["status_none"], ephemeral=True)
            return
        kingdoms = [kingdom for kingdom in await svc.kingdoms() if not kingdom.is_gaia]
        queue = [lord for lord in await svc.lords() if lord.in_queue]
        await interaction.followup.send(
            strings["status_line"].format(
                season=season.id,
                cycle=season.current_cycle + 1,
                weeks=season.weeks,
                kingdoms=len(kingdoms),
                queue=len(queue),
            ),
            ephemeral=True,
        )


def _register_decide_name(
    group: app_commands.Group,
    service: KingdomsService | None,
    guard: Guard,
) -> None:
    """Register the name decision subcommand (D21)."""

    @group.command(
        name=localized(
            "commands.kingdoms_admin_name_name",
            STRINGS[DEFAULT_LOCALE]["name_name"],
        ),
        description=localized(
            "commands.kingdoms_admin_name_description",
            STRINGS[DEFAULT_LOCALE]["name_description"],
        ),
    )
    @app_commands.describe(
        kingdom=localized(
            "commands.kingdoms_admin_name_kingdom_description",
            STRINGS[DEFAULT_LOCALE]["name_kingdom_arg_description"],
        ),
        decision=localized(
            "commands.kingdoms_admin_name_decision_description",
            STRINGS[DEFAULT_LOCALE]["name_decision_arg_description"],
        ),
    )
    @app_commands.choices(
        decision=[
            app_commands.Choice(
                name=localized("commands.kingdoms_admin_name_approve", "Approve"),
                value="approve",
            ),
            app_commands.Choice(
                name=localized("commands.kingdoms_admin_name_refuse", "Refuse"),
                value="refuse",
            ),
        ]
    )
    async def decide_name(
        interaction: discord.Interaction,
        kingdom: str,
        decision: app_commands.Choice[str],
    ) -> None:
        """Approve or refuse a proposed kingdom name (D21)."""
        strings = _strings_for(interaction.locale)
        svc = await _check(interaction, strings, service, guard)
        if svc is None:
            return
        try:
            target = await svc.decide_name(kingdom, decision.value == "approve")
        except KingdomsModError as error:
            await interaction.followup.send(_error_text(interaction.locale, error), ephemeral=True)
            return
        await _answer_decision(interaction, strings, kingdom, decision.value, target)


async def _answer_decision(
    interaction: discord.Interaction,
    strings: dict[str, str],
    kingdom: str,
    decision: str,
    target: KingdomModel,
) -> None:
    """Answer a name decision with the approved or refused message."""
    if decision == "approve":
        await interaction.followup.send(
            strings["name_approved"].format(kingdom=target.name), ephemeral=True
        )
    else:
        await interaction.followup.send(
            strings["name_refused"].format(kingdom=kingdom, fallback=target.name),
            ephemeral=True,
        )
    logger.info("kingdoms: name of %s decided by %s", kingdom, interaction.user.id)


def _register_assign(
    group: app_commands.Group,
    service: KingdomsService | None,
    guard: Guard,
) -> None:
    """Register the queue assignment subcommand (D23)."""

    @group.command(
        name=localized(
            "commands.kingdoms_admin_assign_name",
            STRINGS[DEFAULT_LOCALE]["assign_name"],
        ),
        description=localized(
            "commands.kingdoms_admin_assign_description",
            STRINGS[DEFAULT_LOCALE]["assign_description"],
        ),
    )
    @app_commands.describe(
        player=localized(
            "commands.kingdoms_admin_assign_player_description",
            STRINGS[DEFAULT_LOCALE]["assign_player_arg_description"],
        ),
        kingdom=localized(
            "commands.kingdoms_admin_assign_kingdom_description",
            STRINGS[DEFAULT_LOCALE]["assign_kingdom_arg_description"],
        ),
        role=localized(
            "commands.kingdoms_admin_assign_role_description",
            STRINGS[DEFAULT_LOCALE]["assign_role_arg_description"],
        ),
    )
    @app_commands.choices(
        role=[
            app_commands.Choice(
                name=localized("commands.kingdoms_role_king", "King"),
                value="king",
            ),
            app_commands.Choice(
                name=localized("commands.kingdoms_role_lord", "Lord"),
                value="lord",
            ),
        ]
    )
    async def assign(
        interaction: discord.Interaction,
        player: discord.Member,
        kingdom: str,
        role: app_commands.Choice[str],
    ) -> None:
        """Assign a queued player to a kingdom (admin action, D23)."""
        strings = _strings_for(interaction.locale)
        svc = await _check(interaction, strings, service, guard)
        if svc is None:
            return
        try:
            await svc.assign(
                _member_id(player),
                kingdom,
                LordRole.KING if role.value == "king" else LordRole.LORD,
            )
        except KingdomsModError as error:
            await interaction.followup.send(_error_text(interaction.locale, error), ephemeral=True)
            return
        role_label = strings["enroll_role_king" if role.value == "king" else "enroll_role_lord"]
        await interaction.followup.send(
            strings["assigned"].format(
                player=player.display_name,
                kingdom=kingdom,
                role=role_label,
            ),
            ephemeral=True,
        )
        logger.info("kingdoms: %s assigned by %s", _member_id(player), interaction.user.id)


def _register_replace(
    group: app_commands.Group,
    service: KingdomsService | None,
    guard: Guard,
) -> None:
    """Register the departure replacement subcommand (D23/D25)."""

    @group.command(
        name=localized(
            "commands.kingdoms_admin_replace_name",
            STRINGS[DEFAULT_LOCALE]["replace_name"],
        ),
        description=localized(
            "commands.kingdoms_admin_replace_description",
            STRINGS[DEFAULT_LOCALE]["replace_description"],
        ),
    )
    @app_commands.describe(
        outgoing=localized(
            "commands.kingdoms_admin_replace_outgoing_description",
            STRINGS[DEFAULT_LOCALE]["replace_outgoing_arg_description"],
        ),
        incoming=localized(
            "commands.kingdoms_admin_replace_incoming_description",
            STRINGS[DEFAULT_LOCALE]["replace_incoming_arg_description"],
        ),
    )
    async def replace(
        interaction: discord.Interaction,
        outgoing: discord.Member,
        incoming: discord.Member,
    ) -> None:
        """Replace a departed player with a queued one (D23/D25)."""
        strings = _strings_for(interaction.locale)
        svc = await _check(interaction, strings, service, guard)
        if svc is None:
            return
        try:
            lord = await svc.replace(_member_id(outgoing), _member_id(incoming))
        except KingdomsModError as error:
            await interaction.followup.send(_error_text(interaction.locale, error), ephemeral=True)
            return
        kingdoms = await svc.kingdoms()
        kingdom_name = next(
            (kingdom.name for kingdom in kingdoms if kingdom.id == lord.kingdom_id), ""
        )
        await interaction.followup.send(
            strings["replaced"].format(
                incoming=incoming.display_name,
                outgoing=outgoing.display_name,
                kingdom=kingdom_name,
            ),
            ephemeral=True,
        )
        logger.info(
            "kingdoms: %s replaced %s by admin %s",
            _member_id(incoming),
            _member_id(outgoing),
            interaction.user.id,
        )
