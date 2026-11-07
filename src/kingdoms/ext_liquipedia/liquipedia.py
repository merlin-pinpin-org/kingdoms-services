"""Liquipedia maps provider (kingdoms-services#247).

Fetches map descriptors (info + image) from the official Liquipedia
MediaWiki API — the supported access path; automated access to
generated HTML pages is forbidden by the Liquipedia API terms of use.

Compliance built in (https://liquipedia.net/api-terms-of-use):

- custom identifying User-Agent (generic UAs are blocked),
- rate limit: >= 2 s between requests, >= 30 s between ``action=parse``
  requests,
- gzip + a single reused client, results cached per map (never re-request
  identical data),
- attribution CC-BY-SA 3.0; images whose Liquipedia file description
  page carries a non-free / incompatible licence notice are refused
  (``MapLicenseError``) instead of being republished.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger("kingdoms.ext_liquipedia")

AOE_API_URL = "https://liquipedia.net/ageofempires/api.php"
LICENSE_TERMS_URL = "https://liquipedia.net/api-terms-of-use"

LIQUIPEDIA_ATTRIBUTION = "https://liquipedia.net/ageofempires/"

_MIN_REQUEST_INTERVAL_S = 2.0
_PARSE_INTERVAL_S = 60.0
_429_BACKOFF_BASE_S = 30.0
_429_MAX_RETRIES = 3

# The circuit breaker (the branch's subject: STOP taking 429s): after
# the 429 retries are exhausted the API is telling us, loudly, that
# this IP is unwelcome for a while. Continuing to retry every import
# is pure spam — the circuit OPENS and every call fails fast, zero
# HTTP traffic, until the window lapses. Each consecutive opening
# doubles the window (30 min, 1 h, 2 h... capped at 12 h); one
# successful call resets the streak.
_CIRCUIT_WINDOW_BASE_S = 30 * 60.0
_CIRCUIT_WINDOW_MAX_S = 12 * 3600.0

# Shared circuit state: one breaker per PROCESS (same rationale as the
# pacing clock — a fresh provider per map must not reset it).
_shared_circuit_open_until = 0.0
_shared_circuit_streak = 0

# THE pacer lock: the markers are shared, but the read-sleep-write was
# RACY — two concurrent callers could read the same marker and fire
# together (a burst the ToS punishes with a VPS-wide ban). Every pacing
# decision happens under this lock.
_shared_pacing_lock = threading.Lock()


class LiquipediaCircuitOpenError(RuntimeError):
    """The Liquipedia circuit is open: the API is rate-limiting this IP.

    Every call fails fast while the window is open — no HTTP traffic.
    Retry later; the window doubles on each consecutive opening.
    """

    @classmethod
    def retry_in_s(cls) -> float:
        """Seconds left before the circuit closes (0 when closed)."""
        return max(0.0, _shared_circuit_open_until - time.monotonic())

# Shared pacing state: one clock per PROCESS, not per provider instance.
# fetch_map_seed builds a fresh provider per map (bulk imports, the maps
# sync) \u2014 instance-level markers would reset every time and the
# requests would fire back-to-back, tripping the API into 429s.
_shared_last_request_at = 0.0
_shared_last_parse_at = 0.0

_FIELD_RE = re.compile(r"^\s*\|\s*([A-Za-z0-9_]+)\s*=\s*(.*?)\s*$", re.MULTILINE)
_TEMPLATE_RE = re.compile(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]")
_STRIP_RE = re.compile(r"\{\{[^{}]*\}\}|\[\[(?:[^|\]]*\|)?")
_UNLICENSED_NOTICE_RE = re.compile(r"fair use|non-free|copyright", re.IGNORECASE)
_GAME_TAB_RE = re.compile(r"\|name(\d+)\s*=\s*([^|}\n]+?)\s*\|link\1\s*=\s*([^|}\n]+?)\s*(?=[|}])")
_VERSIONS_SECTION_RE = re.compile(r"==\s*Versions\s*==(.*?)\n==", re.DOTALL)
_BOLD_RE = re.compile(r"'''")
_ITALIC_RE = re.compile(r"''")


@dataclass(frozen=True)
class MapVersion:
    """One named variant of a map over time.

    As listed in the page's Versions section (tournament/terrain
    variants, e.g. KotD3 Arabia, Desert Arabia) — the map's own
    history, distinct from the game-version tabs (AoE2/AoE4
    disambiguation).
    """

    name: str
    description: str = ""
    details: tuple[str, ...] = ()


@dataclass(frozen=True)
class MapInfo:
    """Structured fields extracted from a map's infobox."""

    name: str
    description: str
    terrain: str
    size: str
    map_type: str
    image_file: str
    image_license: str
    source_url: str
    nomad: str = ""
    walls: str = ""
    tc: str = ""
    player_capacity: str = ""
    creator: str = ""
    gallery_icon: str = ""
    game_versions: tuple[str, ...] = ()
    versions: tuple[MapVersion, ...] = ()


@dataclass(frozen=True)
class MapData:
    """A map descriptor ready for storage: info + resolved image URL."""

    info: MapInfo
    image_url: str
    icon_url: str = ""
    attribution: str = LIQUIPEDIA_ATTRIBUTION


class MapLicenseError(RuntimeError):
    """The map's image carries an incompatible upstream licence."""


def query_tags(info: MapInfo) -> tuple[str, ...]:
    """Normalize a map's info into stable, queryable tag values.

    Game-agnostic by construction: every field contributes only when
    non-empty, so the same function serves the next game's provider
    unchanged. Tags are lowercase slugs, safe for Mongo ``$all``
    queries: ``terrain:<v>``, ``type:<v>`` and ``water`` when either
    says hybrid/water.
    """
    tags: list[str] = []
    terrain = info.terrain.strip().lower()
    if terrain:
        tags.append(f"terrain:{terrain}")
    map_type = info.map_type.strip().lower()
    if map_type and map_type != terrain:
        tags.append(f"type:{map_type}")
    if "water" not in tags and any(value in (terrain, map_type) for value in ("hybrid", "water")):
        tags.append("water")
    if info.nomad.strip().lower() in ("yes", "true"):
        tags.append("nomad")
    if info.walls.strip().lower() in ("yes", "partial"):
        tags.append("walled")
    tc = info.tc.strip()
    if tc.isdigit() and int(tc) > 1:
        tags.append(f"tc:{tc}")
    return tuple(tags)


class LiquipediaMapsProvider:
    """Upstream maps data via the Liquipedia MediaWiki API.

    Requests are serialized and rate-limited to honor the API terms of
    use; every fetched map is cached for the process lifetime (re-use /
    cache as long as possible — maps rarely change).
    """

    def __init__(
        self,
        user_agent: str,
        api_url: str = AOE_API_URL,
        timeout_s: float = 10.0,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        if not user_agent or not ("@" in user_agent or "http" in user_agent.lower()):
            raise ValueError(
                "Liquipedia requires a custom User-Agent identifying the "
                "project with contact information "
                "(https://liquipedia.net/api-terms-of-use)"
            )
        self._sleep = sleep if sleep is not None else time.sleep
        self._client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept-Encoding": "gzip"},
            timeout=timeout_s,
        )
        self._api_url = api_url
        self._cache: dict[str, MapData] = {}

    # -- transport ---------------------------------------------------------

    def _rate_limit(self, is_parse: bool = False) -> None:
        """Sleep until the request is allowed by the API terms of use.

        The markers are shared process-wide: every provider instance
        (a bulk import builds one per map) paces against the same
        clock \u2014 the 30 s parse interval holds across the whole
        batch, never per instance.
        """
        global _shared_last_request_at, _shared_last_parse_at
        with _shared_pacing_lock:
            now = time.monotonic()
            interval = _PARSE_INTERVAL_S if is_parse else _MIN_REQUEST_INTERVAL_S
            last = _shared_last_parse_at if is_parse else _shared_last_request_at
            wait = last + interval - now
            if wait > 0:
                self._sleep(wait)
            marker = time.monotonic()
            if is_parse:
                _shared_last_parse_at = marker
            else:
                _shared_last_request_at = marker

    def _get_json(self, params: dict[str, str], is_parse: bool = False) -> dict[str, Any]:
        global _shared_circuit_open_until, _shared_circuit_streak
        if LiquipediaCircuitOpenError.retry_in_s() > 0:
            # FAIL FAST: while the circuit is open, not a single request
            # leaves this process — the API asked for silence, it gets it.
            raise LiquipediaCircuitOpenError(
                "Liquipedia circuit open — the API is rate-limiting this IP; "
                f"retry in {LiquipediaCircuitOpenError.retry_in_s():.0f} s"
            )
        self._rate_limit(is_parse=is_parse)
        response = self._client.get(self._api_url, params=params)
        for attempt in range(_429_MAX_RETRIES):
            if response.status_code != 429:
                break
            # The server-side quota was hit anyway (an earlier burst, a
            # shared IP): honour its Retry-After, at least the
            # exponential backoff, and retry \u2014 a 429 is "later",
            # not "no such page". The shared clock inflates by the same
            # wait so the NEXT map paces behind this one too.
            global _shared_last_request_at, _shared_last_parse_at
            retry_after = float(response.headers.get("Retry-After") or 0.0)
            wait = max(retry_after, _429_BACKOFF_BASE_S * (2**attempt))
            logger.warning(
                "LIQUIPEDIA 429 \u2014 backing off %s s (attempt %d/%d) and retrying",
                wait,
                attempt + 1,
                _429_MAX_RETRIES,
            )
            # DEBUG diagnostics: the full request (URL + params + headers —
            # is the User-Agent right?) and the response's rate-limit
            # headers (which bucket, which scope) to diagnose the 429s.
            logger.debug(
                "LIQUIPEDIA 429 request: GET %s params=%s headers=%s",
                self._api_url,
                params,
                dict(self._client.headers),
            )
            logger.debug(
                "LIQUIPEDIA 429 response headers: %s",
                dict(response.headers),
            )
            self._sleep(wait)
            marker = time.monotonic()
            if is_parse:
                _shared_last_parse_at = marker
            else:
                _shared_last_request_at = marker
            self._rate_limit(is_parse=is_parse)
            response = self._client.get(self._api_url, params=params)
        if response.status_code == 429:
            # The retries are exhausted: the circuit OPENS. Every further
            # call fails fast until the window lapses — no more 429s.
            _shared_circuit_streak += 1
            window = min(
                _CIRCUIT_WINDOW_BASE_S * (2 ** (_shared_circuit_streak - 1)),
                _CIRCUIT_WINDOW_MAX_S,
            )
            _shared_circuit_open_until = time.monotonic() + window
            logger.warning(
                "LIQUIPEDIA 429 exhausted %d retries — CIRCUIT OPEN for %.0f min "
                "(streak %d): every call fails fast until then",
                _429_MAX_RETRIES,
                window / 60.0,
                _shared_circuit_streak,
            )
        elif _shared_circuit_streak:
            # A healthy call after open windows: the streak resets.
            _shared_circuit_streak = 0
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        if "error" in payload:
            raise RuntimeError(f"Liquipedia API error: {payload['error']}")
        return payload

    # -- API ---------------------------------------------------------------

    def ping(self) -> float:
        """Probe the API's reachability, PACED like any other request.

        The health probes (the providers panel refreshes every minute)
        must ride the SAME shared clock as the real fetches — a raw
        unpaced probe per minute eats the ToS budget and collides with
        the live imports from the same IP. Raises on 429/5xx/network
        (the caller reports the probe's outcome); returns the elapsed
        seconds.
        """
        import time as _time

        started = _time.monotonic()
        self._rate_limit()
        response = self._client.get(
            self._api_url,
            params={"action": "query", "meta": "siteinfo", "format": "json"},
        )
        response.raise_for_status()
        return _time.monotonic() - started

    def map_data(self, page_name: str) -> MapData:
        """Return one map's descriptor, from cache when already fetched."""
        cache_key = page_name.strip().replace(" ", "_")
        if cache_key in self._cache:
            return self._cache[cache_key]
        data = self._fetch_map_data(cache_key)
        self._cache[cache_key] = data
        return data

    def _fetch_map_data(self, page: str) -> MapData:
        info = self._fetch_infobox(page)
        image_url = self._resolve_image_url(info.image_file)
        license_notice = self._image_license_notice(info.image_file)
        if _UNLICENSED_NOTICE_RE.search(license_notice):
            raise MapLicenseError(
                f"Image '{info.image_file}' of map '{info.name}' is not "
                "available under a licence compatible with republication; "
                "refusing to import it (Liquipedia CC-BY-SA terms)."
            )
        icon_url = ""
        if info.gallery_icon:
            icon_url = self._resolve_image_url(info.gallery_icon)
        return MapData(info=info, image_url=image_url, icon_url=icon_url)

    def map_data_batch(self, page_names: list[str]) -> dict[str, MapData]:
        """Several maps' descriptors in a few BATCHED action=query calls.

        The API terms (https://liquipedia.net/api-terms-of-use) cap
        ``action=parse`` at one request per 30 s \u2014 one parse per map
        makes a large sync crawl for hours. ``action=query`` instead
        rides the light 2 s pacing and carries up to 50 pages per
        request: the wikitext of the whole batch (``prop=revisions``)
        and the image URLs and licence metadata of every image
        (``prop=imageinfo``) each fit in ONE request per chunk of 50.
        Returns {cache_key: MapData} for the pages that exist; a page
        whose image licence is incompatible is simply absent from the
        result (the single-map path raises MapLicenseError instead \u2014
        the batch caller treats "absent" as "no seed").
        """
        result: dict[str, MapData] = {}
        todo: list[str] = []
        for raw in page_names:
            key = raw.strip().replace(" ", "_")
            if key in self._cache:
                result[key] = self._cache[key]
            elif key not in todo:
                todo.append(key)
        for chunk_start in range(0, len(todo), 50):
            chunk = todo[chunk_start : chunk_start + 50]
            result.update(self._fetch_map_data_batch(chunk))
        return result

    def _wikitext_batch(self, chunk: list[str]) -> dict[str, str]:
        """One ``action=query`` call for the wikitext of \u226450 pages."""
        payload = self._get_json(
            {
                "action": "query",
                "prop": "revisions",
                "rvprop": "content",
                "rvslots": "main",
                "titles": "|".join(chunk),
                "format": "json",
                "formatversion": "2",
            }
        )
        wikitexts: dict[str, str] = {}
        for page in payload.get("query", {}).get("pages", []):
            if page.get("missing"):
                continue
            revisions = page.get("revisions") or []
            if not revisions:
                continue
            slots = revisions[0].get("slots", {})
            content = str((slots.get("main") or {}).get("content", ""))
            key = str(page.get("title", "")).strip().replace(" ", "_")
            wikitexts[key] = content
        return wikitexts

    def _fetch_map_data_batch(self, chunk: list[str]) -> dict[str, MapData]:
        """One wikitext query + two imageinfo queries for \u226450 pages."""
        wikitexts = self._wikitext_batch(chunk)
        infos: dict[str, MapInfo] = {}
        for key in chunk:
            wikitext = wikitexts.get(key)
            if not wikitext:
                continue
            infos[key] = self._info_from_wikitext(key, wikitext)
        if not infos:
            return {}
        # every referenced image file, resolved and licence-checked in
        # one imageinfo=url query + one imageinfo=extmetadata query
        files: list[str] = []
        for info in infos.values():
            for name in (info.image_file, info.gallery_icon):
                if name and f"File:{name}" not in files:
                    files.append(f"File:{name}")
        urls = self._imageinfo_batch(files, "url")
        notices = self._imageinfo_batch(files, "extmetadata")
        out: dict[str, MapData] = {}
        for key, info in infos.items():
            if _UNLICENSED_NOTICE_RE.search(notices.get(info.image_file, "")):
                logger.info(
                    "liquipedia batch: image licence refused for %s \u2014 skipped",
                    info.name,
                )
                continue
            out[key] = MapData(
                info=info,
                image_url=urls.get(info.image_file, ""),
                icon_url=urls.get(info.gallery_icon, "") if info.gallery_icon else "",
            )
        self._cache.update(out)
        return out

    def _imageinfo_batch(self, files: list[str], iiprop: str) -> dict[str, str]:
        """Query imageinfo for up to 50 File: titles at once.

        Returns {file name without the File: prefix: url or licence
        notice} for the ``url`` / ``extmetadata`` iiprop respectively.
        """
        if not files:
            return {}
        out: dict[str, str] = {}
        for chunk_start in range(0, len(files), 50):
            chunk = files[chunk_start : chunk_start + 50]
            payload = self._get_json(
                {
                    "action": "query",
                    "prop": "imageinfo",
                    "titles": "|".join(chunk),
                    "iiprop": iiprop,
                    "format": "json",
                    "formatversion": "2",
                }
            )
            for page in payload.get("query", {}).get("pages", []):
                imageinfo = page.get("imageinfo") or []
                if not imageinfo:
                    continue
                title = str(page.get("title", ""))
                name = title.split("File:", 1)[-1] if title.startswith("File:") else title
                if iiprop == "url":
                    out[name] = str(imageinfo[0].get("url", ""))
                else:
                    meta = imageinfo[0].get("extmetadata", {})
                    out[name] = str((meta.get("LicenseShortName") or {}).get("value", ""))
        return out

    def _fetch_infobox(self, page: str) -> MapInfo:
        payload = self._get_json(
            {
                "action": "parse",
                "page": page,
                "prop": "wikitext",
                "format": "json",
                "formatversion": "2",
            },
            is_parse=True,
        )
        wikitext = str(payload.get("parse", {}).get("wikitext", ""))
        return self._info_from_wikitext(page, wikitext)

    def _info_from_wikitext(self, page: str, wikitext: str) -> MapInfo:
        """Build the descriptor from already-fetched wikitext."""
        fields = self._parse_infobox_fields(wikitext)
        name = fields.get("name") or page.replace("_", " ")
        description = (
            self._lead_paragraph(wikitext)
            or self._clean_wikitext(fields.get("description", ""))
            or (f"Map page on Liquipedia: {LIQUIPEDIA_ATTRIBUTION}{page}")
        )
        return MapInfo(
            name=name,
            description=description,
            terrain=self._clean_wikitext(fields.get("terrain", "")),
            size=self._clean_wikitext(fields.get("size", "")),
            map_type=self._clean_wikitext(fields.get("type", "")),
            image_file=fields.get("image", ""),
            image_license="",
            source_url=f"{LIQUIPEDIA_ATTRIBUTION}{page}",
            nomad=self._clean_wikitext(fields.get("nomad", "")),
            gallery_icon=self._parse_gallery_icon(wikitext),
            walls=self._clean_wikitext(fields.get("walls", "")),
            tc=self._clean_wikitext(fields.get("tc", "")),
            player_capacity=self._clean_wikitext(
                fields.get("player_capacity", "") or fields.get("players", "")
            ),
            creator=self._clean_wikitext(fields.get("creator", "")),
            game_versions=self._parse_game_tabs(wikitext),
            versions=self._parse_versions_section(wikitext),
        )

    @staticmethod
    def _parse_infobox_fields(wikitext: str) -> dict[str, str]:
        """Extract ``|key = value`` pairs from infobox wikitext."""
        fields: dict[str, str] = {}
        for match in _FIELD_RE.finditer(wikitext):
            key = match.group(1).lower()
            if key not in fields:
                fields[key] = match.group(2)
        return fields

    @classmethod
    def _clean_list_markup(cls, value: str) -> str:
        """Clean a Versions-section line.

        Wikilinks keep their label, bold/italic quotes drop,
        templates vanish.
        """
        cleaned = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", value)
        cleaned = re.sub(r"\{\{[^{}]*\}\}", "", cleaned)
        cleaned = _BOLD_RE.sub("", cleaned)
        cleaned = _ITALIC_RE.sub("", cleaned)
        return cleaned.strip()

    @staticmethod
    def _overview_section(wikitext: str) -> str:
        """Return the Overview prose when the page has one, else the lead."""
        overview_at = wikitext.find("==Overview==")
        if overview_at >= 0:
            rest = wikitext[overview_at + len("==Overview==") :]
            return rest.split("\n==")[0]
        return wikitext

    @staticmethod
    def _lead_paragraph(wikitext: str) -> str:
        """Extract the page's description from its wikitext.

        The Overview prose when the page has one, else the bolded lead
        sentence ("Hideout is an Age of Empires II multiplayer map.").
        """
        section = LiquipediaMapsProvider._overview_section(wikitext)
        lines: list[str] = []
        depth = 0
        for line in section.splitlines():
            stripped = line.strip()
            if not stripped or depth > 0 or stripped.startswith("{{"):
                if lines and depth <= 0:
                    break
                depth += stripped.count("{{") - stripped.count("}}")
                continue
            if depth <= 0 and (stripped.startswith("==") or stripped.startswith("<")):
                if lines:
                    break
                continue
            if stripped.startswith("<!--"):
                continue
            lines.append(stripped)
            if len(lines) >= 4:
                break
        text = " ".join(lines)
        text = _BOLD_RE.sub("", text)
        text = _ITALIC_RE.sub("", text)
        text = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", text)
        text = re.sub(r"\{\{[^{}]*\}\}", "", text)
        text = re.sub(r"<[^>]+>", "", text)
        return text.strip()

    @staticmethod
    def _parse_gallery_icon(wikitext: str) -> str:
        """Extract the in-game icon file name from the Gallery.

        The gallery lists one entry per line, in either form:
        "File:Name.png|caption" or the MediaWiki bare "Name.png|caption"
        (Arena's page uses the bare form — the 2026-11-07 merlin report:
        the icon never landed because the parser required the File:
        prefix). The entry captioned as the icon wins; otherwise the
        first entry. Empty when the page has no Gallery.
        """
        block = re.search(r"<gallery[^>]*>(.*?)</gallery>", wikitext, re.S)
        if block is None:
            return ""
        entries = re.findall(
            r"^\s*(?:File:)?([^|\n<]+?)\s*\|([^\n]*)$", block.group(1), re.M
        )
        for filename, caption in entries:
            if "icon" in caption.lower():
                return str(filename.strip())
        return str(entries[0][0].strip()) if entries else ""

    @classmethod
    def _parse_game_tabs(cls, wikitext: str) -> tuple[str, ...]:
        """Extract the game-version tab names (AoE2, AoM, AoE4...).

        The ``{{tabs static}}`` template disambiguates one map across
        games; only the tab names are kept (the link pages are the
        wiki's own navigation, not our data).
        """
        return tuple(cls._clean_wikitext(match.group(2)) for match in _GAME_TAB_RE.finditer(wikitext))

    @classmethod
    def _parse_versions_section(cls, wikitext: str) -> tuple[MapVersion, ...]:
        """Extract the page's ``== Versions ==`` section items.

        One top-level ``*`` item is one named variant of the map over
        time (tournament/terrain variants — KotD3 Arabia, Desert
        Arabia...): the head before the first colon is the name, the
        rest the description; ``**`` sub-items become its ``details``.
        Wiki markup is cleaned off every line. Maps without the
        section carry no variants.
        """
        section = _VERSIONS_SECTION_RE.search(wikitext)
        if section is None:
            return ()
        versions: list[MapVersion] = []
        details: list[str] = []

        def _attach() -> None:
            if versions and details:
                last = versions[-1]
                versions[-1] = MapVersion(name=last.name, description=last.description, details=tuple(details))
                details.clear()

        for raw_line in section.group(1).splitlines():
            line = raw_line.strip()
            if line.startswith("**"):
                cleaned = cls._clean_list_markup(line.lstrip("*"))
                if cleaned and versions:
                    details.append(cleaned)
                continue
            if not line.startswith("*"):
                continue
            _attach()
            cleaned = cls._clean_list_markup(line.lstrip("*")).lstrip()
            head, sep, tail = cleaned.partition(":")
            versions.append(
                MapVersion(
                    name=head.strip(),
                    description=tail.strip() if sep else "",
                )
            )
        _attach()
        return tuple(versions)

    @staticmethod
    def _clean_wikitext(value: str) -> str:
        """Strip wiki markup leftovers from a field value."""
        cleaned = _STRIP_RE.sub("", value)
        cleaned = _TEMPLATE_RE.sub(r"\1", cleaned)
        return cleaned.strip()

    def _resolve_image_url(self, image_file: str) -> str:
        if not image_file:
            return ""
        payload = self._get_json(
            {
                "action": "query",
                "titles": f"File:{image_file}",
                "prop": "imageinfo",
                "iiprop": "url",
                "format": "json",
                "formatversion": "2",
            }
        )
        pages = payload.get("query", {}).get("pages", [])
        if not pages:
            return ""
        imageinfo = pages[0].get("imageinfo", [])
        if not imageinfo:
            return ""
        return str(imageinfo[0].get("url", ""))

    def _image_license_notice(self, image_file: str) -> str:
        """Fetch the image file page's licence metadata (extmetadata)."""
        if not image_file:
            return ""
        payload = self._get_json(
            {
                "action": "query",
                "titles": f"File:{image_file}",
                "prop": "imageinfo",
                "iiprop": "extmetadata",
                "format": "json",
                "formatversion": "2",
            }
        )
        pages = payload.get("query", {}).get("pages", [])
        if not pages:
            return ""
        imageinfo = pages[0].get("imageinfo", [])
        if not imageinfo:
            return ""
        meta = imageinfo[0].get("extmetadata", {})
        return str(meta.get("LicenseShortName", {}).get("value", ""))

    def close(self) -> None:
        """Release the underlying HTTP client (connection reuse ends here)."""
        self._client.close()
