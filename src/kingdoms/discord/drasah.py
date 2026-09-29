"""The /drasah command: a medieval greeting.

First mod authored by a game designer through a vibe-coding session:
it answers the invocation with a medieval-flavored salute, localized
per the guild's locale. No state, no channels, no roles.

Reference: kingdoms-services#<issue>, docs/MODS/drasah/ (kingdoms repo).
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands

from kingdoms.core.services.i18n import MessageCatalog
from kingdoms.discord.commands_i18n import localized

logger = logging.getLogger("kingdoms.drasah")

GREETINGS: dict[str, tuple[str, ...]] = {
    "en": (
        "Hail, {user}! Well met, noble soul, on this fine day!",
        "Greetings, {user}! May thy blade stay sharp and thy tankard full!",
        "Well met, {user}! The kingdom welcomes thee, traveler!",
    ),
    "fr": (
        "Salut, {user} ! Bien rencontré, noble âme, en ce beau jour !",
        "Bonjour, {user} ! Que ta lame reste affûtée et ta chope pleine !",
        "Bien le bonjour, {user} ! Le royaume te souhaite la bienvenue, voyageur !",
    ),
}
DEFAULT_LOCALE = "en"


def greeting_for(locale: str, user: str) -> str:
    """Return a medieval greeting for a locale and user mention.

    Falls back to English when the locale has no greetings.
    """
    variants = GREETINGS.get(locale) or GREETINGS[DEFAULT_LOCALE]
    return variants[hash(user) % len(variants)].format(user=user)


def register_drasah_command(
    tree: app_commands.CommandTree[discord.Client],
    catalog: MessageCatalog | None = None,
) -> None:
    """Register the /drasah slash command on the command tree."""

    @tree.command(
        name=localized("commands.drasah_name", "drasah"),
        description=localized("commands.drasah_description", "Greet the kingdom in medieval fashion"),
    )
    async def drasah_command(interaction: discord.Interaction) -> None:
        """Answer the /drasah interaction with a medieval greeting."""
        locale = "en"
        if interaction.locale is not None:
            candidate = str(interaction.locale)
            if candidate.startswith("fr"):
                locale = "fr"
        user = interaction.user.mention
        await interaction.response.send_message(greeting_for(locale, user))
        logger.info(
            "drasah: greeted %s in guild %s (locale %s)",
            interaction.user.id,
            interaction.guild_id,
            locale,
        )
