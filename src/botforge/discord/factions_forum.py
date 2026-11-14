"""The factions forum: one post per faction, per game (core).

Every game known to the catalog gets, in the ``games`` category, a
``<game>-factions`` forum (e.g. ``aoe2-civs``). Every
non-archived faction gets exactly one post — the faction's public
surface and the future link target for mod-specific faction entities
(the kingdoms mod overlays its own concepts on AoE2 civilizations and
will link to these posts).

The posts are created **without content** on purpose: the goal today
is the forum and its stable link targets, not the description. The
sync is idempotent and self-healing — a deleted post is recreated, an
archived faction's post disappears — and runs on the core
entity-forum engine, so no game or mod is hardcoded here.
"""

from __future__ import annotations

from typing import Any

import discord

from botforge.core.services.entity_forum import EntityForumSpec


def _civ_post_layout(
    name: str,
    civ_type: str,
    sections: list[tuple[str, list[str]]],
    image: str,
    source: str,
    entry_id: str,
    locale: str = "en",
) -> discord.ui.LayoutView:
    """Build the civ post on the shared entity-card template (#321).

    The civ post carries NO admin button: its data is cross-guild
    (the DM panel's sync owns it) and the generic menu had nothing
    useful — the template's action row seam stays free for the civ's
    real future actions (owner direction 2026-11-13).

    The id rides the subtitle (the template's first backticked line),
    the civilization type rides the subtitle's second line, one
    description paragraph per parsed block (the template's dividers
    separate them), the civ image as the header thumbnail, the source
    link and the id footer — ALWAYS the last line. The civ's future
    actions (build orders, counters...) land in the template's action
    row seam when they exist.
    """
    from botforge.discord.ui.entity_cards import civ_card

    source_label = "aoe2techtree" if "aoe2techtree" in source else "source" if source else ""
    return civ_card(
        civ_id=entry_id,
        name=name,
        civ_type=civ_type,
        sections=tuple((label, tuple(items)) for label, items in sections),
        image_url=image,
        source_url=source,
        source_label=source_label,
        locale=locale,
    )


async def _sorted_by_localized_name(factions: list[Any], guild_id: str, bot: Any) -> list[Any]:
    """Sort the factions by their localized name, newest-first.

    The guild's locale drives the name resolution (content store first,
    catalog name fallback); the posts then read in reverse alphabetical
    order of the names the players actually see.
    """
    from botforge.discord.content_posts import guild_locale

    locale = await guild_locale(guild_id, bot)
    service = None
    try:
        from botforge.discord.content_posts import content_service

        service = content_service()
    except Exception:
        service = None

    async def display_name(faction: Any) -> str:
        """Resolve the faction's name in the guild's locale (catalog fallback)."""
        entry_id = str(getattr(faction, "id", ""))
        fallback = str(getattr(faction, "name", faction))
        if service is not None and entry_id:
            try:
                doc = await service.content_get(entry_id, locale)
                if doc and doc.get("name"):
                    return str(doc["name"])
            except Exception:
                return fallback
        return fallback

    import unicodedata

    def sort_key(name: str) -> str:
        """Build the accent-stripped, casefolded sort key of a display name."""
        stripped = unicodedata.normalize("NFKD", name)
        return "".join(c for c in stripped if not unicodedata.combining(c)).casefold()

    keyed: list[tuple[str, Any]] = []
    for faction in factions:
        keyed.append((await display_name(faction), faction))
    keyed.sort(key=lambda pair: sort_key(pair[0]), reverse=True)
    return [faction for _, faction in keyed]


async def _localized_faction_name(faction: Any, guild_id: str, bot: Any = None) -> str:
    """Resolve the faction's display name in the guild's locale.

    The content store holds the localized names (aoe2techtree FR/EN);
    the catalog name is the fallback when no content was synced.
    """
    from botforge.discord.content_posts import content_service, guild_locale

    entry_id = str(getattr(faction, "id", ""))
    fallback = str(getattr(faction, "name", faction))
    service = None
    try:
        service = content_service()
    except Exception:
        service = None
    if service is not None and entry_id:
        try:
            doc = await service.content_get(entry_id, await guild_locale(guild_id, bot))
            if doc and doc.get("name"):
                return str(doc["name"])
        except Exception:
            return fallback
    return fallback


def _game_data(bot: Any) -> Any | None:
    from botforge.discord.wiring import build_games_service

    try:
        return build_games_service()
    except Exception:
        return None


def factions_forum_spec(bot: Any) -> EntityForumSpec:
    """Build the factions forum spec wired onto the games service."""

    async def list_factions(guild_id: str) -> list[Any]:
        """List the guild's granted factions, localized-name sorted."""
        from botforge.discord.wiring import granted_game_keys

        service = _game_data(bot)
        if service is None:
            return []
        factions: list[Any] = []
        for game_key in await granted_game_keys(guild_id, tuple(await service.list_game_keys())):
            factions.extend(await service.list_factions(game_key))
        return await _sorted_by_localized_name(factions, guild_id, bot)

    async def build_post(faction: Any, guild_id: str) -> tuple[str, Any | None]:
        """Build one faction's forum post: content lines and layout view."""
        from botforge.discord.civ_content import civ_section_lines, parse_civ_help
        from botforge.discord.content_posts import entity_post_content

        entry_id = str(getattr(faction, "id", ""))
        fallback = str(getattr(faction, "name", faction))
        name, summary, source, image = await entity_post_content(entry_id, fallback, guild_id, bot)
        parsed = parse_civ_help(summary) if summary else None
        sections = civ_section_lines(parsed) if parsed else []
        civ_type = str(getattr(parsed, "civ_type", "") or "") if parsed else ""
        from botforge.discord.content_posts import guild_locale

        locale = await guild_locale(guild_id, bot)
        view = _civ_post_layout(name, civ_type, sections, image, source, entry_id, locale=locale)
        from botforge.core.ids import footer as id_footer

        content = f"## {name}\n" + (f"_{civ_type}_\n" if civ_type else "")
        if source:
            content += f"{source}\n"
        for label, items in sections:
            content += f"\n**{label}**\n" + "\n".join("\u2022 " + item for item in items) + "\n"
        content += id_footer(entry_id)
        return content, view

    def tree_scope_for(faction: Any) -> str:
        """Scope one faction's forum posts under its game's folder."""
        return f"games/{getattr(faction, 'game_key', '')!s}"

    return EntityForumSpec(
        list_entities=list_factions,
        display_name_for=lambda faction, guild_id: _localized_faction_name(faction, guild_id, bot),
        build_post=build_post,
        tree_scope_for=tree_scope_for,
        tree_channel_for=lambda faction: "civs",
    )
