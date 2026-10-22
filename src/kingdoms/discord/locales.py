"""Native Discord locales: the single resolution seam for every surface.

Discord already owns localization, per guild and per user:

- a guild carries ``guild.preferred_locale`` (Server Settings → Language);
- a user carries ``interaction.locale`` (the client's own language).

The platform therefore stores **no locale of its own**: every surface
renders through this seam, which resolves the locale from the live
Discord objects. The ``set_locale``/``set_user_locale`` settings and
their admin/DM language pickers are gone — an admin who wants the bot
in French sets the **server language** in Discord; a user sets their
**client language**.

Mapping: Discord locales are BCP-47 (``fr``, ``en-US``…); the catalog
carries ``en`` and ``fr``. Anything not French is English.
"""

from __future__ import annotations

import discord

SUPPORTED = ("en", "fr")


def normalize(raw: object) -> str:
    """Map one raw Discord locale onto the catalog (en/fr, en fallback)."""
    code = str(raw or "").split("-")[0].lower()
    return code if code in SUPPORTED else "en"


def guild_locale(guild: discord.Guild | None) -> str:
    """Return the guild's native locale (Server Settings → Language)."""
    return normalize(getattr(guild, "preferred_locale", None))


def interaction_locale(interaction: discord.Interaction) -> str:
    """Return the locale of an interaction-driven (ephemeral) surface.

    The clicker's own client language wins — Discord localizes slash
    commands the same way — then the server's language when the client
    speaks a language the catalog does not carry, then English.
    """
    client_code = normalize(getattr(interaction, "locale", None))
    if client_code in SUPPORTED:
        return client_code
    guild = getattr(interaction, "guild", None)
    preferred = getattr(guild, "preferred_locale", None)
    if preferred:
        return normalize(preferred)
    return client_code


def guild_locale_by_id(bot: object, guild_id: str) -> str:
    """Resolve a guild's native locale from its id (live object only)."""
    get_guild = getattr(bot, "get_guild", None)
    if not guild_id or not str(guild_id).isdigit() or get_guild is None:
        return "en"
    guild = get_guild(int(guild_id))
    return guild_locale(guild)


async def stored_guild_locale(bot: object, guild_id: str) -> str:
    """Resolve a guild's effective locale: the STORED choice first.

    The language picker's value is the guild's explicit choice — it
    wins over the Discord Server Settings signal; the native locale
    only seeds the guilds that never picked. The content surfaces
    (the posts, the per-locale reads) resolve through this seam.
    """
    logs = getattr(bot, "logs_service", None)
    if logs is not None and guild_id:
        try:
            stored = str(await logs.get_locale(guild_id) or "")
            if stored in SUPPORTED:
                return stored
        except Exception:  # noqa: S110 - degrade to the native signal
            pass
    return guild_locale_by_id(bot, guild_id)


__all__ = [
    "SUPPORTED",
    "guild_locale",
    "guild_locale_by_id",
    "interaction_locale",
    "normalize",
    "stored_guild_locale",
]
