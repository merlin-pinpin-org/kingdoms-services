"""ext-liquipedia — Liquipedia content provider process.

A real provider (ADR-0020/0021), not an in-process module: it owns the
Liquipedia MediaWiki access (terms-of-use compliance, rate limiting,
licence guard) and its own Mongo tables — the raw fetched data lives
here, exploited and served over ``kingdoms.v1.Content``. Liquipedia is
a multi-game source (AoE2 today; the wiki family covers every esport),
so the map seeding is keyed by ``game_key``.

Tables owned (logical base ``liquipedia``):
- ``liquipedia_maps``  — one document per (game, page): the parsed
  descriptor, the raw wikitext, licence outcome, fetched_at — the debug
  and replay source of every import;
- ``liquipedia_pages`` — the raw MediaWiki API responses (cache-aside:
  a page already fetched is never re-requested until the TTL lapses).
"""

from __future__ import annotations

PROVIDER_ID = "ext-liquipedia"
PROVIDER_KEY = "liquipedia"
GAME_KEY = "aoe2"
SUPPORTED_LOCALES = ("en",)

MAPS_COLLECTION = "liquipedia_maps"
PAGES_COLLECTION = "liquipedia_pages"

PAGE_CACHE_TTL_S = 7 * 24 * 3600
