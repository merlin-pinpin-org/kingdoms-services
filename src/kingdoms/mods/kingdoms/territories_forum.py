"""The kingdoms territories forum: one post per territory, per season.

Territories are **not** maps: a territory is backed by one AoE2 map
but carries a mod-specific function (ownership, protection, attacks).
Each gets a visible id (``territory:<season id>:<map key>``, built by
``kingdoms.core.ids``) and one post in the ``kingdoms-territories``
forum (``games`` category), built through the core's generic
entity-forum engine — no bespoke sync loop here. Every post links
back to its map's ``aoe2-maps`` forum post and shows the territory's
ids in a footer, so a designer can quote them in admin and seed
commands.

The sync is idempotent and self-healing: a deleted post is recreated,
a redrawn territory keeps its id, a post for a vanished territory is
removed. Only the **current season's** territories are surfaced.
"""

from __future__ import annotations

import logging
from typing import Any

from kingdoms.core.services.entity_forum import EntityForumSpec

logger = logging.getLogger("kingdoms.mods.kingdoms.territories_forum")

FORUM_NAME = "kingdoms-territories"


def _territories_lister(bot: Any) -> Any:
    """Resolve the mod's territory service from the bot wiring."""

    async def list_territories(guild_id: str) -> list[Any]:
        service = getattr(bot, "territories_service", None)
        kingdoms_service = getattr(bot, "kingdoms_service", None)
        if service is None or kingdoms_service is None:
            return []
        try:
            season = await kingdoms_service.current_season()
        except Exception:
            return []
        if season is None:
            return []
        territories = await service.territories()
        return [t for t in territories if t.season_id == season.id]

    return list_territories


def territories_forum_spec(bot: Any) -> EntityForumSpec:
    """Build the territories forum spec wired onto the bot's services."""

    async def build_post(territory: Any, guild_id: str) -> tuple[str, Any | None]:
        from kingdoms.core.ids import footer, territory_id
        from kingdoms.mods.kingdoms.config import normalize_map_key
        from kingdoms.mods.kingdoms.models import TerritoryModel

        model = territory if isinstance(territory, TerritoryModel) else TerritoryModel.from_mongo(territory)
        game_data = getattr(bot, "kingdoms_game_data", None)
        kingdoms_service = getattr(bot, "kingdoms_service", None)
        map_name = model.map_key
        map_link = ""
        if game_data is not None:
            try:
                from kingdoms.mods.kingdoms import GAME_KEY

                entries = await game_data.list_maps(GAME_KEY)
                entry = next(
                    (e for e in entries if normalize_map_key(e.name) == model.map_key),
                    None,
                )
                if entry is not None:
                    map_name = entry.name
                    if entry.forum_message_id:
                        map_link = (
                            f"\n- [fiche de la map](https://discord.com/channels/"
                            f"{guild_id}/{entry.forum_message_id}/{entry.forum_message_id})"
                        )
            except Exception:
                logger.debug("territories forum: map lookup failed", exc_info=True)
        owner = ""
        if kingdoms_service is not None:
            try:
                kingdoms = await kingdoms_service.kingdoms()
                found = next((k for k in kingdoms if k.id == model.owner_kingdom_id), None)
                if found is not None:
                    owner = f"\nPropriétaire : **{found.name}**"
            except Exception:
                logger.debug("territories forum: kingdom lookup failed", exc_info=True)
        tid = model.id or territory_id(model.season_id, model.map_key)
        content = (
            f"**{map_name}**\n"
            f"Territoire de la saison — adossé à la map {map_name}."
            f"{map_link}{owner}\n\n{footer(tid, model.season_id)}"
        )
        return content, None

    return EntityForumSpec(
        forum_name=FORUM_NAME,
        list_entities=_territories_lister(bot),
        build_post=build_post,
    )
