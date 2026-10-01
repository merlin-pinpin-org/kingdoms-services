"""The /kingdoms command group: the game designer's Lot B screens.

Every subcommand defers first (the 3-second rule), then answers on the
followup with a Components V2 layout built through the UI SDK — never
discord.ui classes, never content= on a V2 message (the wire rule).

Season state reads are Lot C wiring: until a season is launched every
screen renders the no-season placeholder — the screen structure is
real, the season data arrives with the T2 launch (reference §3, D38/D52).
Strings follow the drasah pattern (designer-authored, FR/EN dict) for
the runtime screens; the command names and descriptions are
localizable through the shared catalog (``commands.kingdoms_*`` keys
in ``config/locales/<locale>.yaml``), so a French client sees
/kingdoms cadastre, royaume, delais, diplomatie, gazette.
"""
from __future__ import annotations

import logging

import discord
from discord import app_commands

from kingdoms.discord.commands_i18n import localized
from kingdoms.mods.kingdoms.models import LordModel, LordRole
from kingdoms.mods.kingdoms.service import KingdomsModError, KingdomsService
from kingdoms.mods.kingdoms.snapshot import kingdom_card, season_label
from kingdoms.mods.kingdoms.views import (
    build_kingdom_profile_layout,
    build_no_season_layout,
)

logger = logging.getLogger("kingdoms.kingdoms_command")

DEFAULT_LOCALE = "en"

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "group_description": "Kingdoms season screens: cadastre, kingdom, delays, diplomacy, gazette",
        "kingdom_arg": "kingdom",
        "kingdom_arg_description": "The kingdom to display (your own if omitted)",
        "cadastre_name": "cadastre",
        "cadastre_description": "Territory ownership map",
        "cadastre_title": "🧾 Cadastre",
        "profile_name": "kingdom",
        "profile_description": "A kingdom's profile: king, lords, tech",
        "profile_title": "👑 Kingdom",
        "delays_name": "delays",
        "delays_description": "Programmed attacks and countdowns",
        "delays_title": "🕰️ Attack delays",
        "diplomacy_name": "diplomacy",
        "diplomacy_description": "Alliances and the technology shop",
        "diplomacy_title": "🤝 Diplomacy",
        "gazette_name": "gazette",
        "gazette_description": "The weekly cycle summary",
        "gazette_title": "📣 The Gazette",
        "no_season": (
            "No season is running yet. An admin will launch the season soon "
            "— live data will show here once it starts."
        ),
        "footer": "Kingdoms — AoE2 territory conquest",
        "enroll_name": "join",
        "enroll_description": "Enroll in the season: King or Lord (D22)",
        "enroll_role": "role",
        "enroll_role_description": "King founds a kingdom; a Lord joins one",
        "enroll_role_king": "King",
        "enroll_role_lord": "Lord",
        "enroll_kingdom": "kingdom",
        "enroll_kingdom_description": "The kingdom to join (empty = waiting queue)",
        "enroll_proposed_name": "name",
        "enroll_proposed_name_description": "A King proposes the kingdom name (admin-validated)",
        "leave_name": "leave",
        "leave_description": "Leave the current season (with a reason)",
        "leave_reason": "reason",
        "leave_reason_description": "Why you leave (shown to the admins)",
        "service_unavailable": "The kingdoms service is not available on this deployment.",
        "enrolled_king": (
            "👑 You founded **{kingdom}** as its King — an admin validates the name soon."
        ),
        "enrolled_lord": "🎖️ You joined **{kingdom}** as a Lord.",
        "enrolled_queue": "🕰️ You are enrolled in the waiting queue — an admin assigns you soon.",
        "left_season": "You left the season. An admin may assign a replacement.",
        "no_kingdom": (
            "No kingdom to display: name an existing kingdom, or enroll first "
            "(`/kingdoms join`)."
        ),
        "errors": {
            "no_season": "No season is running — an admin launches it with `/kingdoms-admin launch`.",
            "already_enrolled": "You are already enrolled in the current season.",
            "name_invalid": "This kingdom name breaks the configured rules (length or characters).",
            "kingdom_limit": "The season already counts its maximum of kingdoms.",
            "imposed": "Kingdoms are imposed this season — an admin assigns you.",
            "kingdom_not_found": "No kingdom with this name in the current season.",
            "kingdom_full": "This kingdom already counts its maximum of Lords.",
            "not_enrollable": "Gaïa kingdoms never enroll players.",
            "not_queued": "This player is not waiting in the queue.",
            "replacement": "The outgoing player has not left the season yet.",
            "unexpected": "An unexpected error occurred. Try again.",
        },
    },
    "fr": {
        "group_description": "Écrans de la saison Kingdoms : cadastre, royaume, délais, diplomatie, gazette",
        "kingdom_arg": "royaume",
        "kingdom_arg_description": "Le royaume à afficher (le vôtre si omis)",
        "cadastre_name": "cadastre",
        "cadastre_description": "Carte des territoires et de leurs propriétaires",
        "cadastre_title": "🧾 Cadastre",
        "profile_name": "royaume",
        "profile_description": "Profil d'un royaume : roi, seigneurs, technologies",
        "profile_title": "👑 Royaume",
        "delays_name": "delais",
        "delays_description": "Attaques programmées et comptes à rebours",
        "delays_title": "🕰️ Délais d'attaque",
        "diplomacy_name": "diplomatie",
        "diplomacy_description": "Alliances et boutique des technologies",
        "diplomacy_title": "🤝 Diplomatie",
        "gazette_name": "gazette",
        "gazette_description": "Le résumé hebdomadaire du cycle",
        "gazette_title": "📣 La Gazette",
        "no_season": (
            "Aucune saison n'est en cours. Un admin lancera la saison prochainement "
            "— les données réelles s'afficheront ici dès le lancement."
        ),
        "footer": "Kingdoms — conquête de territoires AoE2",
        "enroll_name": "inscrire",
        "enroll_description": "S'inscrire à la saison : Roi ou Seigneur (D22)",
        "enroll_role": "role",
        "enroll_role_description": "Le Roi fonde un royaume ; le Seigneur en rejoint un",
        "enroll_role_king": "Roi",
        "enroll_role_lord": "Seigneur",
        "enroll_kingdom": "royaume",
        "enroll_kingdom_description": "Le royaume à rejoindre (vide = file d'attente)",
        "enroll_proposed_name": "nom",
        "enroll_proposed_name_description": "Le Roi propose le nom du royaume (validé par l'admin)",
        "leave_name": "quitter",
        "leave_description": "Quitter la saison en cours (avec un motif)",
        "leave_reason": "motif",
        "leave_reason_description": "Pourquoi vous partez (transmis aux admins)",
        "service_unavailable": "Le service kingdoms n'est pas disponible sur ce déploiement.",
        "enrolled_king": (
            "👑 Vous avez fondé **{kingdom}** comme Roi — un admin valide le nom prochainement."
        ),
        "enrolled_lord": "🎖️ Vous avez rejoint **{kingdom}** comme Seigneur.",
        "enrolled_queue": "🕰️ Vous êtes inscrit en file d'attente — un admin vous affectera prochainement.",
        "left_season": "Vous avez quitté la saison. Un admin peut désigner un remplaçant.",
        "no_kingdom": (
            "Aucun royaume à afficher : nommez un royaume existant, ou inscrivez-vous "
            "d'abord (`/kingdoms inscrire`)."
        ),
        "errors": {
            "no_season": "Aucune saison n'est en cours — un admin la lance avec `/kingdoms-admin lancer`.",
            "already_enrolled": "Vous êtes déjà inscrit dans la saison en cours.",
            "name_invalid": "Ce nom de royaume ne respecte pas les règles configurées (longueur ou caractères).",
            "kingdom_limit": "La saison compte déjà son nombre maximum de royaumes.",
            "imposed": "Les royaumes sont imposés cette saison — un admin vous affecte.",
            "kingdom_not_found": "Aucun royaume de ce nom dans la saison en cours.",
            "kingdom_full": "Ce royaume compte déjà son nombre maximum de Seigneurs.",
            "not_enrollable": "Les royaumes Gaïa n'inscrivent jamais de joueurs.",
            "not_queued": "Ce joueur n'est pas en file d'attente.",
            "replacement": "Le joueur sortant n'a pas encore quitté la saison.",
            "unexpected": "Une erreur inattendue est survenue. Réessayez.",
        },
    },
}


def _strings_for(locale: discord.Locale | None) -> dict[str, str]:
    """Pick the string set for an interaction locale (English fallback)."""
    if locale is not None and str(locale).startswith("fr"):
        return STRINGS["fr"]
    return STRINGS[DEFAULT_LOCALE]


def _error_text(strings: dict[str, str], error: KingdomsModError) -> str:
    """Resolve a mod error into its designer-authored message."""
    return strings["errors"].get(error.message_key.split(".")[-1], strings["errors"]["unexpected"])


async def _send_placeholder(
    interaction: discord.Interaction,
    strings: dict[str, str],
    title: str,
) -> None:
    """Defer, then answer with the no-season placeholder screen."""
    await interaction.response.defer()
    view = build_no_season_layout(
        title,
        strings["no_season"],
        footer=strings["footer"],
    )
    await interaction.followup.send(view=view)
    logger.info(
        "kingdoms: %s screen served to %s in guild %s",
        title,
        interaction.user.id,
        interaction.guild_id,
    )


def register_kingdoms_command(
    tree: app_commands.CommandTree[discord.Client],
    service: KingdomsService | None = None,
) -> None:
    """Register the /kingdoms command group on the command tree."""
    group = app_commands.Group(
        name=localized("commands.kingdoms_name", "kingdoms"),
        description=localized(
            "commands.kingdoms_description",
            STRINGS[DEFAULT_LOCALE]["group_description"],
        ),
        guild_only=True,
    )
    tree.add_command(group)
    _register_placeholder_screen(group, "cadastre", "cadastre_title")
    _register_profile_screen(group, service)
    _register_placeholder_screen(group, "delays", "delays_title")
    _register_diplomacy_screen(group)
    _register_placeholder_screen(group, "gazette", "gazette_title")
    _register_enroll(group, service)
    _register_leave(group, service)


def _register_placeholder_screen(
    group: app_commands.Group,
    screen: str,
    title_key: str,
) -> None:
    """Register one of the Lot B placeholder screens on the group."""

    @group.command(
        name=localized(
            f"commands.kingdoms_{screen}_name",
            STRINGS[DEFAULT_LOCALE][f"{screen}_name"],
        ),
        description=localized(
            f"commands.kingdoms_{screen}_description",
            STRINGS[DEFAULT_LOCALE][f"{screen}_description"],
        ),
    )
    async def screen_command(interaction: discord.Interaction) -> None:
        """Serve the placeholder screen until a season runs."""
        strings = _strings_for(interaction.locale)
        await _send_placeholder(interaction, strings, strings[title_key])


def _register_profile_screen(
    group: app_commands.Group,
    service: KingdomsService | None,
) -> None:
    """Register the B2 kingdom profile screen (live once a season runs)."""

    @group.command(
        name=localized(
            "commands.kingdoms_kingdom_name",
            STRINGS[DEFAULT_LOCALE]["profile_name"],
        ),
        description=localized(
            "commands.kingdoms_kingdom_description",
            STRINGS[DEFAULT_LOCALE]["profile_description"],
        ),
    )
    @app_commands.describe(
        kingdom=localized(
            "commands.kingdoms_kingdom_arg_description",
            STRINGS[DEFAULT_LOCALE]["kingdom_arg_description"],
        )
    )
    async def profile(
        interaction: discord.Interaction,
        kingdom: str | None = None,
    ) -> None:
        """B2 — the kingdom profile screen (live once a season runs)."""
        strings = _strings_for(interaction.locale)
        await interaction.response.defer()
        if service is None:
            await interaction.followup.send(strings["service_unavailable"], ephemeral=True)
            return
        await _send_profile(interaction, strings, service, kingdom)


async def _send_profile(
    interaction: discord.Interaction,
    strings: dict[str, str],
    service: KingdomsService,
    kingdom: str | None,
) -> None:
    """Render the live profile screen, or its no-season/no-kingdom fallbacks."""
    season = await service.current_season()
    if season is None:
        view = build_no_season_layout(
            strings["profile_title"],
            strings["no_season"],
            footer=strings["footer"],
        )
        await interaction.followup.send(view=view)
        return
    target = await service.resolve_kingdom(kingdom, str(interaction.user.id))
    if target is None:
        await interaction.followup.send(strings["no_kingdom"])
        return
    lords = [lord for lord in await service.lords() if lord.kingdom_id == target.id and not lord.left]
    card = kingdom_card(target, lords, territory_count=0, tech_points=0)
    locale = "fr" if str(interaction.locale or "").startswith("fr") else "en"
    label = season_label(season, service.config, locale=locale)
    view = build_kingdom_profile_layout(strings["profile_title"], card, footer=label)
    await interaction.followup.send(view=view)
    logger.info(
        "kingdoms: profile of %s served to %s in guild %s",
        target.name,
        interaction.user.id,
        interaction.guild_id,
    )


def _register_diplomacy_screen(group: app_commands.Group) -> None:
    """Register the B4 diplomacy screen (placeholder until a season runs)."""

    @group.command(
        name=localized(
            "commands.kingdoms_diplomacy_name",
            STRINGS[DEFAULT_LOCALE]["diplomacy_name"],
        ),
        description=localized(
            "commands.kingdoms_diplomacy_description",
            STRINGS[DEFAULT_LOCALE]["diplomacy_description"],
        ),
    )
    @app_commands.describe(
        kingdom=localized(
            "commands.kingdoms_kingdom_arg_description",
            STRINGS[DEFAULT_LOCALE]["kingdom_arg_description"],
        )
    )
    async def diplomacy(
        interaction: discord.Interaction,
        kingdom: str | None = None,
    ) -> None:
        """B4 — the diplomacy screen (placeholder until a season runs)."""
        del kingdom  # the argument freezes the signature; the placeholder ignores it
        strings = _strings_for(interaction.locale)
        await _send_placeholder(interaction, strings, strings["diplomacy_title"])


def _register_enroll(
    group: app_commands.Group,
    service: KingdomsService | None,
) -> None:
    """Register the T2 enroll subcommand (reference section 5)."""

    @group.command(
        name=localized(
            "commands.kingdoms_enroll_name",
            STRINGS[DEFAULT_LOCALE]["enroll_name"],
        ),
        description=localized(
            "commands.kingdoms_enroll_description",
            STRINGS[DEFAULT_LOCALE]["enroll_description"],
        ),
    )
    @app_commands.describe(
        role=localized(
            "commands.kingdoms_enroll_role_description",
            STRINGS[DEFAULT_LOCALE]["enroll_role_description"],
        ),
        kingdom=localized(
            "commands.kingdoms_enroll_kingdom_description",
            STRINGS[DEFAULT_LOCALE]["enroll_kingdom_description"],
        ),
        name=localized(
            "commands.kingdoms_enroll_name_description",
            STRINGS[DEFAULT_LOCALE]["enroll_proposed_name_description"],
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
    async def enroll(
        interaction: discord.Interaction,
        role: app_commands.Choice[str],
        kingdom: str | None = None,
        name: str | None = None,
    ) -> None:
        """T2 — enroll as King or Lord, or wait in the queue (reference section 5)."""
        strings = _strings_for(interaction.locale)
        await interaction.response.defer()
        if service is None:
            await interaction.followup.send(strings["service_unavailable"], ephemeral=True)
            return
        try:
            lord = await service.enroll(
                str(interaction.user.id),
                interaction.user.display_name,
                LordRole.KING if role.value == "king" else LordRole.LORD,
                kingdom_name=kingdom,
                proposed_name=name,
            )
        except KingdomsModError as error:
            await interaction.followup.send(_error_text(strings, error), ephemeral=True)
            return
        await _answer_enrollment(interaction, strings, service, lord)


async def _answer_enrollment(
    interaction: discord.Interaction,
    strings: dict[str, str],
    service: KingdomsService,
    lord: LordModel,
) -> None:
    """Answer an enrollment with its designer-authored outcome message."""
    kingdoms = await service.kingdoms()
    joined = next((k for k in kingdoms if k.id == lord.kingdom_id), None)
    if lord.role is LordRole.KING:
        await interaction.followup.send(
            strings["enrolled_king"].format(kingdom=joined.name if joined else "")
        )
    elif lord.kingdom_id is not None:
        await interaction.followup.send(
            strings["enrolled_lord"].format(kingdom=joined.name if joined else "")
        )
    else:
        await interaction.followup.send(strings["enrolled_queue"])
    logger.info(
        "kingdoms: %s enrolled as %s in guild %s",
        interaction.user.id,
        lord.role,
        interaction.guild_id,
    )


def _register_leave(
    group: app_commands.Group,
    service: KingdomsService | None,
) -> None:
    """Register the T2 leave subcommand (D23)."""

    @group.command(
        name=localized(
            "commands.kingdoms_leave_name",
            STRINGS[DEFAULT_LOCALE]["leave_name"],
        ),
        description=localized(
            "commands.kingdoms_leave_description",
            STRINGS[DEFAULT_LOCALE]["leave_description"],
        ),
    )
    @app_commands.describe(
        reason=localized(
            "commands.kingdoms_leave_reason_description",
            STRINGS[DEFAULT_LOCALE]["leave_reason_description"],
        )
    )
    async def leave(
        interaction: discord.Interaction,
        reason: str | None = None,
    ) -> None:
        """T2 — leave the season with a reason (D23)."""
        strings = _strings_for(interaction.locale)
        await interaction.response.defer()
        if service is None:
            await interaction.followup.send(strings["service_unavailable"], ephemeral=True)
            return
        try:
            await service.leave(str(interaction.user.id), reason or "")
        except KingdomsModError as error:
            await interaction.followup.send(_error_text(strings, error), ephemeral=True)
            return
        await interaction.followup.send(strings["left_season"])
        logger.info(
            "kingdoms: %s left the season in guild %s",
            interaction.user.id,
            interaction.guild_id,
        )
