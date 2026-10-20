"""Kingdoms mod — civilization crest images (civ-crests-v1, Drasah 20/10).

The mod's player-facing civ lists (the 📜 Alliances pinned draft starter,
the pick & ban REVEAL DM, the battle-room meeting message) read as bare
names. Drasah's 2026-10-20 report: the platform database carries each
civilization's blason (``faction:aoe2:<Civ>`` content docs →
``image_url``), and the texts should wear them.

Discord cannot inline a remote image inside a line of text, so the
crests ride as ATTACHED files: they display under the message body, in
the same order as the names. The PNGs come from the licence-clean
aoe2techtree repository — the very URL the content docs store — so no
new upstream is introduced.

The mod's catalog keys are Drasah's French slugs (``azteques``) while
the dataset and the content docs speak the provider's keys
(``Aztecs``); :data:`_CIV_CREST_PROVIDER_KEYS` bridges the two. A
missing mapping or a failed download is cosmetic-only: the message
goes out regardless, minus that crest.
"""
from __future__ import annotations

import io
import logging
from typing import Any

import discord
import httpx

logger = logging.getLogger("kingdoms.kingdoms.civ_crests")

CREST_BASE_URL = "https://raw.githubusercontent.com/SiegeEngineers/aoe2techtree/master/img/Civs"
MAX_ATTACHMENTS = 10

_CIV_CREST_PROVIDER_KEYS: dict[str, str] = {
    "azteques": "Aztecs",
    "bengalis": "Bengalis",
    "bourguignons": "Burgundians",
    "britanniques": "Britons",
    "celtes": "Celts",
    "ethiopiens": "Ethiopians",
    "georgiens": "Georgians",
    "huns": "Huns",
    "incas": "Incas",
    "italiens": "Italians",
    "japonais": "Japanese",
    "khmers": "Khmer",
    "malais": "Malay",
    "maliens": "Malians",
    "mapuche": "Mapuche",
    "mayas": "Mayans",
    "mongols": "Mongols",
    "perses": "Persians",
    "polonais": "Poles",
    "sarrasins": "Saracens",
    "siciliens": "Sicilians",
    "slaves": "Slavs",
    "danois": "Danes",
    "varegues": "Varangians",
    "saxons": "Saxons",
    "tupi": "Tupi",
    "vikings": "Vikings",
    "muisca": "Muisca",
    "armeniens": "Armenians",
    "dravidiens": "Dravidians",
    "berberes": "Berbers",
    "coumans": "Cumans",
    "espagnols": "Spanish",
    "francs": "Franks",
    "jurchen": "Jurchens",
    "khitan": "Khitans",
    "tatars": "Tatars",
    "teutons": "Teutons",
    "goths": "Goths",
    "hindoustanis": "Hindustanis",
    "bulgares": "Bulgarians",
    "lituaniens": "Lithuanians",
    "gurjaras": "Gurjaras",
    "coreens": "Koreans",
    "byzantins": "Byzantines",
    "romains": "Romans",
    "turcs": "Turks",
    "chinois": "Chinese",
    "shu": "Shu",
    "wei": "Wei",
    "wu": "Wu",
    "magyars": "Magyars",
    "birmans": "Burmese",
    "bohemiens": "Bohemians",
    "portugais": "Portuguese",
}

_CREST_CACHE: dict[str, bytes] = {}


def civ_crest_url(civ_key: str) -> str | None:
    """Return the crest image URL of one mod civilization key (None unknown)."""
    provider_key = _CIV_CREST_PROVIDER_KEYS.get(str(civ_key).strip().lower())
    if provider_key is None:
        return None
    return f"{CREST_BASE_URL}/{provider_key.lower()}.png"


async def _crest_bytes(url: str) -> bytes | None:
    """Download one crest PNG (best effort, in-memory cached).

    The cache keeps the provisioning ticks cheap: the same civ list
    re-announced after a restart or a refresh never re-downloads.
    Tests monkeypatch this seam to avoid real network traffic.
    """
    cached = _CREST_CACHE.get(url)
    if cached is not None:
        return cached
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.content
    except Exception:
        logger.warning("CIV CRESTS: download failed (%s)", url, exc_info=True)
        return None
    _CREST_CACHE[url] = data
    return data


async def civ_crest_files(civ_keys: Any) -> list[discord.File]:
    """Build the crest attachments for one civ list (order preserved).

    Unknown keys, download failures and the 10-attachment Discord cap
    degrade silently: the message always goes out, dressed with what
    could be fetched.
    """
    files: list[discord.File] = []
    seen: set[str] = set()
    for civ_key in civ_keys or ():
        key = str(civ_key).strip()
        if not key or key in seen or len(files) >= MAX_ATTACHMENTS:
            continue
        seen.add(key)
        url = civ_crest_url(key)
        if url is None:
            continue
        data = await _crest_bytes(url)
        if data is None:
            continue
        files.append(discord.File(io.BytesIO(data), filename=f"{key}.png"))
    return files
