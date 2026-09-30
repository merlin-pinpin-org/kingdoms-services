"""The /kingdoms command group: the game designer's Lot B screens.

Every subcommand defers first (the 3-second rule), then answers on the
followup with a Components V2 layout built through the UI SDK — never
discord.ui classes, never content= on a V2 message (the wire rule).

Season state reads are Lot C wiring: until the season-launch workflow
lands, every subcommand renders the no-season placeholder — the screen
structure is real, the season data is not yet (reference §3, D38/D52).
Strings follow the drasah pattern (designer-authored, FR/EN dict).

Command names and descriptions are registered with Discord native
localizations: a French client sees /kingdoms cadastre, royaume, delais,
diplomatie, gazette; an English client sees the English names —
per-user locale, inside the guild.
"""
from __future__ import annotations

import logging

import discord
from discord import app_commands

from kingdoms.mods.kingdoms.views import build_no_season_layout

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
    },
}


def _strings_for(locale: discord.Locale | None) -> dict[str, str]:
    """Pick the string set for an interaction locale (English fallback)."""
    if locale is not None and str(locale).startswith("fr"):
        return STRINGS["fr"]
    return STRINGS[DEFAULT_LOCALE]


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


def _fr(key: str) -> str:
    """Return the French variant of a registered string."""
    return STRINGS["fr"][key]


def register_kingdoms_command(tree: app_commands.CommandTree[discord.Client]) -> None:
    """Register the /kingdoms command group on the command tree."""
    group = app_commands.Group(
        name="kingdoms",
        description=STRINGS[DEFAULT_LOCALE]["group_description"],
        description_localizations={discord.Locale.french: _fr("group_description")},
        guild_only=True,
    )
    tree.add_command(group)

    @group.command(
        name=STRINGS[DEFAULT_LOCALE]["cadastre_name"],
        name_localizations={discord.Locale.french: _fr("cadastre_name")},
        description=STRINGS[DEFAULT_LOCALE]["cadastre_description"],
        description_localizations={discord.Locale.french: _fr("cadastre_description")},
    )
    async def cadastre(interaction: discord.Interaction) -> None:
        """B1 — the cadastre screen (placeholder until a season runs)."""
        strings = _strings_for(interaction.locale)
        await _send_placeholder(interaction, strings, strings["cadastre_title"])

    @group.command(
        name=STRINGS[DEFAULT_LOCALE]["profile_name"],
        name_localizations={discord.Locale.french: _fr("profile_name")},
        description=STRINGS[DEFAULT_LOCALE]["profile_description"],
        description_localizations={discord.Locale.french: _fr("profile_description")},
    )
    @app_commands.describe(kingdom=STRINGS[DEFAULT_LOCALE]["kingdom_arg_description"])
    async def profile(
        interaction: discord.Interaction,
        kingdom: str | None = None,
    ) -> None:
        """B2 — the kingdom profile screen (placeholder until a season runs)."""
        del kingdom  # the argument freezes the signature; the placeholder ignores it
        strings = _strings_for(interaction.locale)
        await _send_placeholder(interaction, strings, strings["profile_title"])

    @group.command(
        name=STRINGS[DEFAULT_LOCALE]["delays_name"],
        name_localizations={discord.Locale.french: _fr("delays_name")},
        description=STRINGS[DEFAULT_LOCALE]["delays_description"],
        description_localizations={discord.Locale.french: _fr("delays_description")},
    )
    async def delays(interaction: discord.Interaction) -> None:
        """B3 — the attack delays screen (placeholder until a season runs)."""
        strings = _strings_for(interaction.locale)
        await _send_placeholder(interaction, strings, strings["delays_title"])

    @group.command(
        name=STRINGS[DEFAULT_LOCALE]["diplomacy_name"],
        name_localizations={discord.Locale.french: _fr("diplomacy_name")},
        description=STRINGS[DEFAULT_LOCALE]["diplomacy_description"],
        description_localizations={discord.Locale.french: _fr("diplomacy_description")},
    )
    @app_commands.describe(kingdom=STRINGS[DEFAULT_LOCALE]["kingdom_arg_description"])
    async def diplomacy(
        interaction: discord.Interaction,
        kingdom: str | None = None,
    ) -> None:
        """B4 — the diplomacy screen (placeholder until a season runs)."""
        del kingdom  # the argument freezes the signature; the placeholder ignores it
        strings = _strings_for(interaction.locale)
        await _send_placeholder(interaction, strings, strings["diplomacy_title"])

    @group.command(
        name=STRINGS[DEFAULT_LOCALE]["gazette_name"],
        name_localizations={discord.Locale.french: _fr("gazette_name")},
        description=STRINGS[DEFAULT_LOCALE]["gazette_description"],
        description_localizations={discord.Locale.french: _fr("gazette_description")},
    )
    async def gazette(interaction: discord.Interaction) -> None:
        """B5 — the Gazette screen (placeholder until a season runs)."""
        strings = _strings_for(interaction.locale)
        await _send_placeholder(interaction, strings, strings["gazette_title"])

    return None
