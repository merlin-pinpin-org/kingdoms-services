"""Slash command localizations, sourced from the shared yaml catalog.

Discord renders a command's name and description per client locale. In
discord.py that goes through :class:`app_commands.locale_str` markers
plus a tree :class:`app_commands.Translator` — the single integration
point with the platform's i18n. The source of truth stays the shared
catalog: ``commands.<name>_name`` / ``commands.<name>_description`` in
``config/locales/<locale>.yaml``, never a hardcoded dict in the command
module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.app_commands import locale_str

if TYPE_CHECKING:
    from kingdoms.core.services.i18n import MessageCatalog


def localized(key: str, default: str) -> locale_str:
    """Mark a command name/description as localizable under ``key``.

    ``default`` is the English string Discord falls back to; the
    catalog keys live under the ``commands.`` yaml section.
    """
    return locale_str(default, key=key)


class CatalogTranslator(app_commands.Translator):
    """Translate ``locale_str`` markers through the MessageCatalog.

    Attached once per tree (``tree.set_translator``); discord.py calls
    it at sync time for every locale Discord requests. Unknown keys or
    a missing catalog translate to ``None`` — Discord keeps the
    default string, the command never breaks.
    """

    def __init__(self, catalog: MessageCatalog | None) -> None:
        """Store the catalog the translations resolve through."""
        self._catalog = catalog

    async def translate(
        self,
        string: locale_str,
        locale: discord.Locale,
        context: app_commands.TranslationContextTypes,
    ) -> str | None:
        """Resolve a ``locale_str`` marker for one Discord locale."""
        key = str(string.extras.get("key", ""))
        if self._catalog is None or not key.startswith("commands."):
            return None
        rendered = self._catalog.render(key, locale.value)
        if not rendered or rendered == key:
            return None
        return rendered


async def reply_locale(interaction: discord.Interaction) -> str:
    """Resolve the guild's configured locale for one interaction."""
    bot = getattr(interaction, "client", None)
    logs = getattr(bot, "logs_service", None)
    guild_id = str(getattr(interaction, "guild_id", "") or "")
    if logs is None or not guild_id:
        return "en"
    try:
        return str(await logs.get_locale(guild_id) or "en")
    except Exception:
        return "en"


async def reply(interaction: discord.Interaction, key: str, **kwargs: object) -> str:
    """Render a ``replies.*`` message in the guild's locale (English fallback)."""
    catalog = getattr(getattr(interaction, "client", None), "messages", None)
    locale = await reply_locale(interaction)
    if catalog is None:
        return key
    return catalog.render(f"replies.{key}", locale, **kwargs)
