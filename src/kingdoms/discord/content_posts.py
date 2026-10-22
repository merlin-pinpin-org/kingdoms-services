"""Entity-post content resolution: locale-aware, Redis then Mongo (core).

The forums' posts (maps, factions) carry localized content seeded from
a licence-compatible provider (see ``kingdoms.core.services.faction_content``).
This module resolves, at post build time, the guild's content for one
entity: the guild's locale (en fallback), then the content through the
cache-aside service — Redis first, Mongo second. When no content is
stored the entity's catalog name stands in and the summary is empty,
so a fresh environment still gets its posts.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("kingdoms.discord.content_posts")

_CONTENT_SERVICE: Any = None
_CONTENT_READY = False


def _build_content_service() -> Any | None:
    """Build the FactionContentService over the shared Mongo adapter."""
    import os

    from kingdoms.core.services.faction_content import FactionContentService

    if not os.environ.get("MONGO_URI"):
        return None
    try:
        from kingdoms.core.models.db import get_async_database

        return FactionContentService(get_async_database())
    except Exception:
        logger.warning("content service unavailable", exc_info=True)
        return None


def content_service() -> Any | None:
    """Memoize the content service (None when Mongo is absent)."""
    global _CONTENT_SERVICE, _CONTENT_READY
    if _CONTENT_READY:
        return _CONTENT_SERVICE
    _CONTENT_READY = True
    _CONTENT_SERVICE = _build_content_service()
    return _CONTENT_SERVICE


async def guild_locale(guild_id: str, bot: Any) -> str:
    """Resolve the guild's native Discord locale (Server Settings language)."""
    from kingdoms.discord.locales import stored_guild_locale

    return await stored_guild_locale(bot, str(guild_id))


async def entity_post_content(entity_id: str, fallback_name: str, guild_id: str, bot: Any) -> tuple[str, str, str, str]:
    """Return ``(name, summary, source_url, image_url)`` for one entity's post.

    The stored content wins; the catalog entry's name is the fallback.
    """
    service = content_service()
    locale = await guild_locale(guild_id, bot)
    if service is None:
        return fallback_name, "", "", ""
    try:
        doc = await service.get(entity_id, locale)
        if doc is None and locale != "en":
            # The map imports store their content under the "en" locale —
            # a guild reading in another locale still gets the card.
            doc = await service.get(entity_id, "en")
    except Exception:
        logger.debug("content read failed (%s)", entity_id, exc_info=True)
        doc = None
    if doc is None:
        return fallback_name, "", "", ""
    source_url = str(doc.get("source_url") or "")
    if "aoe2techtree.net" in source_url and "?" not in source_url:
        # the site localizes through ?lng= — carry the guild's locale
        source_url = f"{source_url.split('#', 1)[0]}?lng={locale}" + (
            f"#{source_url.split('#', 1)[1]}" if "#" in source_url else ""
        )
    return (
        str(doc.get("name") or fallback_name),
        str(doc.get("summary") or ""),
        source_url,
        str(doc.get("image_url") or ""),
    )
