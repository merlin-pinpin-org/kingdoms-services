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

import json
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("kingdoms.core.faction_content")

PROVIDER_KEY = "aoe2techtree"
SOURCE_URL = "https://github.com/SiegeEngineers/aoe2techtree"
SUPPORTED_LOCALES = ("en", "fr")


@dataclass(frozen=True, slots=True)
class LocalizedContent:
    """One entity's localized content, ready for a forum post."""

    entity_id: str
    locale: str
    name: str
    summary: str
    source_url: str


class TechtreeContentProvider:
    """Extract per-faction and per-map localized content from aoe2techtree."""

    def __init__(self, dataset: dict[str, Any], strings: dict[str, dict[str, str]]) -> None:
        """Store the dataset and the per-locale string tables."""
        self._dataset = dataset
        self._strings = strings

    @classmethod
    def from_files(cls, data_json: str, locale_files: dict[str, str]) -> TechtreeContentProvider:
        """Build from the raw file contents (data.json + locale strings)."""
        dataset = json.loads(data_json)
        strings = {lng: json.loads(raw) for lng, raw in locale_files.items()}
        return cls(dataset, strings)

    def faction_content(self, faction_name: str, locale: str) -> LocalizedContent | None:
        """Build one faction's localized content descriptor.

        The dataset carries each civ's ``name_string_id`` and
        ``help_string_id`` (the game's own civ help: strengths, bonuses,
        unique unit) — both resolved in the requested locale, falling
        back to English. The help text's ``<br>`` markers become
        newlines for the Discord post.
        """
        civ = (self._dataset.get("civs") or {}).get(faction_name)
        if civ is None:
            logger.debug("techtree provider: unknown faction %s", faction_name)
            return None
        table = self._strings.get(locale) or self._strings.get("en") or {}
        localized = str(table.get(str(civ.get("name_string_id")), faction_name))
        help_raw = str(table.get(str(civ.get("help_string_id")), ""))
        summary = help_raw.replace("<br>", "\n").strip()
        return LocalizedContent(
            entity_id=f"faction:aoe2:{faction_name}",
            locale=locale,
            name=localized,
            summary=summary,
            source_url=SOURCE_URL,
        )

    def faction_names(self) -> list[str]:
        """List every civ name known to the dataset."""
        return list((self._dataset.get("civs") or {}).keys())

    def map_content(self, map_name: str, locale: str) -> LocalizedContent | None:
        """Build one map's localized content descriptor (name only today)."""
        del locale
        return LocalizedContent(
            entity_id=f"map:aoe2:{map_name}",
            locale="en",
            name=map_name,
            summary="",
            source_url=SOURCE_URL,
        )
