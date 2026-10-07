"""The per-kingdom category provisioning (tranche ① of the Season II design).

D70 (kingdoms repo DECISIONS.md): each player kingdom owns a private
Discord category — ``Royaume <Nom>`` — created when the admin approves
the proposed name (D21), holding eight state-view channels. The Salle
du Conseil is the only talking channel; the others are silent state
views the coming tranches fill (patrouille, église, pigeon…).

Gaïa never gets this category: its channels are the declared static
``Royaume Gaïa`` group (Gaïa only defends — D70), provisioned by the
core bootstrap like every declared channel.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import discord

from kingdoms.discord.kingdom_setup import _find_category, _slug

logger = logging.getLogger("kingdoms.kingdom_structure")

KINGDOM_CATEGORY_PREFIX = "Royaume "
"""The display prefix of every per-kingdom category (D70)."""

GAIA_CATEGORY = "Royaume Gaïa"
"""The static Gaïa group name; a kingdom named like this is refused."""


@dataclass(frozen=True, slots=True)
class KingdomChannelDef:
    """One channel of the per-kingdom category, by key and display name."""

    key: str
    display_name: str


KINGDOM_CHANNELS: tuple[KingdomChannelDef, ...] = (
    KingdomChannelDef(key="conseil", display_name="💬 Salle du Conseil"),
    KingdomChannelDef(key="patrouille", display_name="🛡️ Patrouille"),
    KingdomChannelDef(key="seigneurs", display_name="🎖️ Seigneurs"),
    KingdomChannelDef(key="le-royaume", display_name="🏰 Le-Royaume"),
    KingdomChannelDef(key="territoire", display_name="🗺️ Territoire"),
    KingdomChannelDef(key="alliances", display_name="📜 Alliances"),
    KingdomChannelDef(key="eglise", display_name="⛪ Église"),
    KingdomChannelDef(key="pigeon", display_name="🕊️ Pigeon-Voyageur"),
)
"""The eight channels of a kingdom category (D70 — order is the salon order)."""


def kingdom_category_name(kingdom_name: str) -> str:
    """Return the Discord category name of one kingdom."""
    return f"{KINGDOM_CATEGORY_PREFIX}{kingdom_name}"


def _find_channel_in(
    category: discord.CategoryChannel, name: str
) -> discord.abc.GuildChannel | None:
    """Find one channel inside a category by slug; None when absent."""
    wanted = _slug(name)
    for channel in getattr(category, "channels", []):
        if _slug(getattr(channel, "name", "")) == wanted:
            return channel  # type: ignore[no-any-return]
    return None


def _kingdom_overwrites(
    guild: discord.Guild, member_ids: tuple[str, ...]
) -> dict[Any, discord.PermissionOverwrite]:
    """Build the kingdom category overwrites (D70 visibility).

    The category and its channels are visible to the kingdom's members
    (the King and his Lords — resolved by id at creation time) and the
    bot; @everyone cannot see them. The admins ride the bot's
    management surface, not a Discord role.
    """
    overwrites: dict[Any, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
    }
    for member_id in member_ids:
        member = guild.get_member(int(member_id)) if member_id.isdigit() else None
        if member is not None:
            overwrites[member] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
    if guild.me is not None:
        overwrites[guild.me] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            manage_channels=True,
            manage_messages=True,
        )
    return overwrites


async def _kingdom_member_ids(kingdoms_service: Any, kingdom_id: str) -> tuple[str, ...]:
    """Resolve the Discord ids of a kingdom's active lords (King first)."""
    member_ids: list[str] = []
    try:
        lords = await kingdoms_service.lords()
    except Exception:
        logger.warning("KINGDOM STRUCTURE: lord resolution failed", exc_info=True)
        return ()
    for lord in lords:
        if getattr(lord, "kingdom_id", None) == kingdom_id and not getattr(lord, "left", False):
            member_ids.append(str(lord.id))
    return tuple(member_ids)


async def ensure_kingdom_structure(
    guild: discord.Guild,
    kingdoms_service: Any,
    kingdom_id: str,
    kingdom_name: str,
) -> list[str]:
    """Create (or adopt) the private category of one kingdom (D70).

    Called at the admin's name approval (D21). Idempotent: an existing
    category is adopted and missing channels are completed, so a
    re-run after a partial failure converges. Returns the resolved
    channel names (created and adopted alike).
    """
    if not kingdom_name.strip() or kingdom_category_name(kingdom_name) == GAIA_CATEGORY:
        return []
    member_ids = await _kingdom_member_ids(kingdoms_service, kingdom_id)
    category_name = kingdom_category_name(kingdom_name)
    category = _find_category(guild, category_name)
    if category is None:
        category = await guild.create_category(
            category_name,
            reason=f"kingdoms: category of {kingdom_name}",
            overwrites=_kingdom_overwrites(guild, member_ids),
        )
    resolved: list[str] = []
    for channel_def in KINGDOM_CHANNELS:
        existing = _find_channel_in(category, channel_def.display_name)
        if existing is None:
            created = await guild.create_text_channel(
                channel_def.display_name,
                reason=f"kingdoms: {channel_def.key} of {kingdom_name}",
                category=category,
            )
            resolved.append(created.name)
        else:
            resolved.append(existing.name)
    return resolved
