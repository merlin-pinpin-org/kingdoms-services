"""Create-or-enrich helpers for map entries (kingdoms-services#247).

``create_map_from_name`` implements the game-designer flow: given a map
name, try the Liquipedia provider first (public data + image); when the
page doesn't exist (or its image licence is incompatible), fall back to a
guild-local entry with admin-entered fields. The created entry is stored
in the catalog through the regular service, so forums and pools pick it
up like any other map.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger("kingdoms.mapsdata")

DEFAULT_USER_AGENT_TEMPLATE = "KingdomsBot/1.0 (https://github.com/merlin-pinpin-org/kingdoms; {contact})"


def _user_agent() -> str:
    """Build the identified User-Agent; the contact comes from config."""
    contact = os.environ.get("LIQUIPEDIA_CONTACT", "")
    if not contact:
        return ""
    return DEFAULT_USER_AGENT_TEMPLATE.format(contact=contact)


@dataclass(frozen=True)
class MapSeed:
    """The fields a provider resolves for one map name."""

    name: str
    description: str
    resource_url: str
    image_url: str
    terrain: str = ""
    size: str = ""
    public: bool = True


async def fetch_map_seed(name: str) -> MapSeed | None:
    """Resolve one map name into a seed via Liquipedia (best-effort).

    Returns None when Liquipedia doesn't know the page or isn't
    configured (no contact email) — the caller then creates a guild-local
    entry with the fields typed in Discord.
    """
    user_agent = _user_agent()
    if not user_agent:
        logger.debug("liquipedia seed skipped: LIQUIPEDIA_CONTACT unset")
        return None
    from kingdoms.mapsdata.liquipedia import (
        LIQUIPEDIA_ATTRIBUTION,
        LiquipediaMapsProvider,
        MapLicenseError,
    )

    provider = LiquipediaMapsProvider(user_agent)
    try:
        data = provider.map_data(name)
        return MapSeed(
            name=data.info.name,
            description=data.info.description,
            resource_url=data.info.source_url,
            image_url=data.image_url,
            terrain=data.info.terrain,
            size=data.info.size,
            public=True,
        )
    except MapLicenseError:
        logger.info("liquipedia seed: image licence refused for %s", name)
        seed = MapSeed(
            name=name,
            description="",
            resource_url=LIQUIPEDIA_ATTRIBUTION + name.replace(" ", "_"),
            image_url="",
            public=True,
        )
        return seed
    except Exception:
        logger.info("liquipedia seed: no page for %s", name, exc_info=True)
        return None
    finally:
        provider.close()
