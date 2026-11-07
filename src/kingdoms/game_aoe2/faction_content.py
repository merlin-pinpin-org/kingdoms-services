"""Faction/map localized-content provider: aoe2techtree extraction (core).

The content source for AoE2 factions and maps is the **aoe2techtree**
open-source dataset (SiegeEngineers/aoe2techtree, MIT licence, data
extracted from the game under Microsoft's Game Content Usage Rules).
It ships per-locale string files (``data/locales/<lng>/strings.json``)
and a per-civ tree index (``data/data.json``) — a public git
repository, not a protected backend API: no scraping of a live site,
no ToS-grey zone, no AI summarization. Only the extracted,
licence-compatible fields are used.

The provider turns that dataset into per-faction, per-locale content
descriptors (localized name + the civ's unique units), addressed by
the catalog's stable keys (``faction:aoe2:Franks``). The locale string
id of each civ is resolved from the data itself (the English string
table maps each civ name to its id), never hardcoded.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("kingdoms.core.faction_content")

PROVIDER_KEY = "aoe2techtree"
CREST_BASE_URL = "https://raw.githubusercontent.com/SiegeEngineers/aoe2techtree/master/img/Civs"


def _civ_crest_url(provider_key: str) -> str:
    """Build the civ's crest image URL from the aoe2techtree repository."""
    return f"{CREST_BASE_URL}/{provider_key.lower()}.png"


SOURCE_URL = "https://aoe2techtree.net"
LIQUIPEDIA_MAP_URL = "https://liquipedia.net/ageofempires/"


def _civ_page_url(provider_key: str) -> str:
    """Build the civ's own page URL on aoe2techtree (the per-civ anchor)."""
    return f"{SOURCE_URL}/#{provider_key}"


SUPPORTED_LOCALES = ("en", "fr")
