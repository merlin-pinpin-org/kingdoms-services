"""ext-liquipedia source: Liquipedia access + the provider's own tables.

The ``LiquipediaMapsProvider`` (the provider-owned HTTP client)
carries the MediaWiki access with its terms-of-use compliance (custom
User-Agent, rate limits, licence guard). This module wraps it with the
provider's persistence: every fetched page is stored raw in Mongo
(``liquipedia_pages``, cache-aside with a TTL) and every resolved map
descriptor in ``liquipedia_maps`` — the raw data lives at the provider,
debuggable and replayable, and a repeated import never re-requests a
fresh page while the cache holds.

The in-memory cache of the underlying provider stays (the process
lifetime); Mongo adds the cross-restart durability the in-process
module never had.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from concurrent.futures import Future
from typing import Any

from kingdoms.ext_liquipedia import (
    GAME_WIKIS,
    MAPS_COLLECTION,
    PAGE_CACHE_TTL_S,
    PAGES_COLLECTION,
)
from kingdoms.ext_liquipedia.liquipedia import (
    LiquipediaMapsProvider,
)
from kingdoms.ext_liquipedia.liquipedia import (
    query_tags as _query_tags,
)

# A pending seed request: (game_key, the title variants, the future the
# waiting map_seed call blocks on).
_SeedRequest = tuple[str, tuple[str, ...], "Future[dict[str, Any] | None]"]

logger = logging.getLogger("kingdoms.ext_liquipedia")

# The seed COALESCING window: concurrent importers (one admin's N maps,
# another's M) are collected for up to this long and served by ONE
# batched action=query (<=50 pages, the light 2 s pacing) instead of a
# burst of single-page action=parse calls (60 s pacing each — the ToS
# cap). Map imports are patient: a few seconds of latency buy a
# fraction of the API budget (owner direction 2026-11-05).
_SEED_WINDOW_S = 3.0
_SEED_MAX_BATCH = 50


class LiquipediaSource:
    """The Content source: Liquipedia access + Mongo-backed cache."""

    def __init__(self, providers: dict[str, LiquipediaMapsProvider], database: Any) -> None:
        """Wire the per-wiki upstream access and the provider's own database.

        The source is game-AGNOSTIC: ``providers`` maps a wiki family
        (one HTTP endpoint each, the pacing clock stays shared process-
        wide); the game_key of the request picks the wiki. It never
        knows who consumes it.
        """
        self._providers = providers
        self._db = database
        # The cross-request seed batcher: concurrent map_seed calls queue
        # here and the worker serves them in batched queries.
        self._seed_queue: queue.Queue[_SeedRequest] = queue.Queue()
        self._seed_worker = threading.Thread(
            target=self._seed_worker_loop, name="liquipedia-seeds", daemon=True
        )
        self._seed_worker.start()

    def faction_keys(self) -> list[str]:
        """Liquipedia serves maps, not factions — the contract degrades."""
        return []

    def faction_content(self, key: str, locale: str) -> dict[str, Any] | None:
        """No faction content on this source."""
        return None

    def map_content(self, key: str, locale: str, game_key: str = "aoe2") -> dict[str, Any] | None:
        """Serve one map's localized content (name + attribution link)."""
        seed = self.map_seed(game_key, key)
        if seed is None:
            return None
        return {
            "entity_id": f"map:{game_key}:{key}",
            "locale": locale or "en",
            "name": str(seed.get("name", key)),
            "summary": str(seed.get("description", "")),
            "source_url": str(seed.get("resource_url", "")),
            "found": True,
        }

    def map_seed(self, game_key: str, map_key: str) -> dict[str, Any] | None:
        """Resolve one map name into its full descriptor.

        The persisted descriptor serves first (cache-aside); a miss is
        QUEUED for the seed worker, which coalesces every concurrent
        request into batched ``action=query`` calls (<=50 pages each,
        the light 2 s pacing — never a burst of single-page ``parse``
        calls). A page absent upstream answers None (the caller falls
        back to admin-entered fields).
        """
        from kingdoms.core.wiki_names import extract_page_name, title_variants

        name = extract_page_name(map_key)
        cached = self._load_descriptor(game_key, name)
        if cached is not None:
            logger.info("SEED: %r (game %s) — cache hit", name, game_key)
            return cached
        if game_key not in self._providers:
            logger.info("SEED: %r (game %s) — no wiki declared, nothing to serve", name, game_key)
            return None  # no wiki declared for this game: nothing to serve
        logger.info("SEED: %r (game %s) — fetching upstream", name, game_key)
        future: Future[dict[str, Any] | None] = Future()
        self._seed_queue.put((game_key, tuple(title_variants(name)), future))
        result = future.result()
        if result is None:
            logger.warning("SEED: %r (game %s) — NOT FOUND upstream", name, game_key)
        else:
            logger.info("SEED: %r (game %s) — resolved upstream", name, game_key)
        return result

    def _seed_worker_loop(self) -> None:
        """Serve the queued seed requests in batches, forever.

        Collects for up to the coalescing window (or the batch cap),
        then ONE ``map_data_batch`` per game_key — concurrent importers
        share the same batched call, the pacing clock serializes the
        batches, and the circuit breaker guards the whole thing.
        """
        while True:
            try:
                batch: list[_SeedRequest] = [self._seed_queue.get()]
                deadline = time.monotonic() + _SEED_WINDOW_S
                while len(batch) < _SEED_MAX_BATCH:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    try:
                        batch.append(self._seed_queue.get(timeout=remaining))
                    except queue.Empty:
                        break
                self._serve_seed_batch(batch)
            except Exception:
                # the worker never dies: a broken batch surfaces on its
                # own futures, the next one starts clean
                logger.exception("liquipedia seed worker: batch failed")

    def _serve_seed_batch(self, batch: list[_SeedRequest]) -> None:
        """One batched fetch per game_key; every future gets its answer.

        WHATEVER happens: an unexpected error in the descriptor build or
        the persistence must never leave a future unresolved — a waiting
        map_seed call would block forever. The catch-all fails every
        unfinished future of the batch; the worker lives on.
        """
        futures = [future for _, _, future in batch]
        try:
            by_game: dict[str, list[_SeedRequest]] = {}
            for entry in batch:
                by_game.setdefault(entry[0], []).append(entry)
            for game_key, requests in by_game.items():
                self._serve_seed_batch_for_game(game_key, requests)
        except BaseException as exc:
            for future in futures:
                if not future.done():
                    future.set_exception(exc)

    def _serve_seed_batch_for_game(
        self,
        game_key: str,
        requests: list[_SeedRequest],
    ) -> None:
        """One batched fetch for every waiting request of one game."""
        provider = self._providers.get(game_key)
        if provider is None:
            for _, _, future in requests:
                future.set_result(None)
            return
        # the union of every title variant, deduplicated: ONE query
        variants: list[str] = []
        seen: set[str] = set()
        for _, req_variants, _ in requests:
            for variant in req_variants:
                key = variant.strip().replace(" ", "_")
                if key not in seen:
                    seen.add(key)
                    variants.append(variant)
        try:
            data = provider.map_data_batch(variants)
        except Exception as exc:
            # the licence guard and the open circuit are NOT "page not
            # found": they reach every waiting request
            for _, _, future in requests:
                future.set_exception(exc)
            return
        for _, req_variants, future in requests:
            hit_variant = next(
                (v for v in req_variants if v.strip().replace(" ", "_") in data), None
            )
            if hit_variant is None:
                logger.info(
                    "SEED WORKER: %r (game %s) — page absent upstream",
                    req_variants[0] if req_variants else "?",
                    game_key,
                )
                future.set_result(None)
                continue
            hit = data[hit_variant.strip().replace(" ", "_")]
            descriptor = _descriptor_of(game_key, hit_variant, hit)
            self._persist(game_key, hit_variant, hit, descriptor)
            logger.info("SEED WORKER: %r (game %s) — fetched and persisted", hit_variant, game_key)
            future.set_result(descriptor)

    def _load_descriptor(self, game_key: str, name: str) -> dict[str, Any] | None:
        """Read the freshest persisted descriptor within the page TTL."""
        try:
            doc = self._db[MAPS_COLLECTION].find_one(
                {"game_key": game_key, "$or": [{"name": name}, {"page": name.replace(" ", "_")}]}
            )
        except Exception:
            return None
        if not doc:
            return None
        if time.time() - float(doc.get("fetched_at", 0)) > PAGE_CACHE_TTL_S:
            return None
        return dict(doc.get("descriptor") or {}) or None

    def _persist(
        self, game_key: str, page: str, data: Any, descriptor: dict[str, Any]
    ) -> None:
        """Store the raw page data and the resolved descriptor."""
        now = time.time()
        try:
            self._db[PAGES_COLLECTION].update_one(
                {"_id": f"{game_key}:{page}"},
                {"$set": {"game_key": game_key, "page": page, "fetched_at": now}},
                upsert=True,
            )
            self._db[MAPS_COLLECTION].update_one(
                {"_id": f"{game_key}:{page}"},
                {
                    "$set": {
                        "game_key": game_key,
                        "page": page,
                        "name": str(data.info.name),
                        "image_url": str(data.image_url),
                        "icon_url": str(data.icon_url),
                        "terrain": str(data.info.terrain),
                        "size": str(data.info.size),
                        "map_type": str(data.info.map_type),
                        "licence_ok": True,
                        "fetched_at": now,
                        "descriptor": descriptor,
                    }
                },
                upsert=True,
            )
        except Exception:
            logger.warning("liquipedia persist failed (page %s) — best-effort", page, exc_info=True)


class _EmptyInfo:
    """A shapeless page's info: every field empty, nothing crashes."""


def _descriptor_of(game_key: str, page: str, data: Any) -> dict[str, Any]:
    """Flatten one MapData into the wire-shaped descriptor dict.

    Defensive per field: a page with an odd shape degrades to defaults,
    never an exception (one malformed page must not fail its batch's
    other requests).
    """
    def _safe(build: Any) -> Any:
        try:
            return build()
        except Exception:
            return ""

    def _safe_list(build: Any) -> list[Any]:
        try:
            return list(build())
        except Exception:
            return []

    info: Any = getattr(data, "info", None) or _EmptyInfo()
    return {
        "name": _safe(lambda: str(info.name)),
        "description": _safe(lambda: str(info.description)),
        "resource_url": _safe(lambda: str(info.source_url)),
        "image_url": _safe(lambda: str(data.image_url)),
        "icon_url": _safe(lambda: str(data.icon_url)),
        "terrain": _safe(lambda: str(info.terrain)),
        "size": _safe(lambda: str(info.size)),
        "map_type": _safe(lambda: str(info.map_type)),
        # the enrichment surface: the maps sync rides the provider too
        # (one Liquipedia client per env — never a second in the bot)
        # the bools read the label's MEANING, never its emptiness —
        # bool("No") is True in Python (the 2026-11-06 bug: every
        # non-empty label became True, Arabia showed Walls/Nomad: Oui)
        "walls": str(getattr(info, "walls", "")).strip().lower() in ("yes", "partial", "true"),
        "nomad": str(getattr(info, "nomad", "")).strip().lower() in ("yes", "true"),
        "tc": str(getattr(info, "tc", "")).strip().isdigit(),
        # the RAW labels (owner direction 2026-11-06): the infobox's own
        # words — walls "No"/"Partial", tc "1"/"2", capacity "Up to 8" —
        # never amputated into bools
        "walls_label": _safe(lambda: str(info.walls)),
        "nomad_label": _safe(lambda: str(info.nomad)),
        "tc_label": _safe(lambda: str(info.tc)),
        "player_capacity": _safe(lambda: str(getattr(info, "player_capacity", ""))),
        "creator": _safe(lambda: str(getattr(info, "creator", ""))),
        "game_versions": _safe_list(lambda: list(getattr(info, "game_versions", ()) or ())),
        "versions": _safe_list(
            lambda: [
                {"name": v.name, "description": v.description, "details": list(v.details)}
                for v in getattr(info, "versions", ()) or ()
            ]
        ),
        "tags": _safe_list(lambda: list(_query_tags(info))),
        "licence_ok": True,
        "licence_note": "",
    }


def resolve_source(database: Any) -> LiquipediaSource:
    """Build the source from the environment (PROVIDER_CONTACT)."""
    from kingdoms.core.wiki_names import wiki_user_agent as _user_agent

    user_agent = _user_agent()
    if not user_agent:
        raise RuntimeError(
            "PROVIDER_CONTACT is not set — the Liquipedia terms of use "
            "require an identified User-Agent (https://liquipedia.net/api-terms-of-use)"
        )
    providers = {
        game: LiquipediaMapsProvider(user_agent, api_url=f"https://liquipedia.net/{wiki}/api.php")
        for game, wiki in GAME_WIKIS.items()
    }
    return LiquipediaSource(providers, database)
