"""Map-data providers (kingdoms-services#247).

A provider fetches upstream map descriptors (info + image) through a
supported API — never HTML scraping. The Liquipedia provider implements
the upstream contract: identified User-Agent, rate-limited requests,
response caching and licence attribution.
"""

from kingdoms.mapsdata.liquipedia import (
    LIQUIPEDIA_ATTRIBUTION,
    LiquipediaMapsProvider,
    MapData,
    MapInfo,
    MapLicenseError,
)

__all__ = [
    "LIQUIPEDIA_ATTRIBUTION",
    "LiquipediaMapsProvider",
    "MapData",
    "MapInfo",
    "MapLicenseError",
]
