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
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger("kingdoms.mapsdata")

AOE_API_URL = "https://liquipedia.net/ageofempires/api.php"
LICENSE_TERMS_URL = "https://liquipedia.net/api-terms-of-use"

LIQUIPEDIA_ATTRIBUTION = "https://liquipedia.net/ageofempires/"

_MIN_REQUEST_INTERVAL_S = 2.0
_PARSE_INTERVAL_S = 30.0

_FIELD_RE = re.compile(r"^\s*\|\s*([A-Za-z0-9_]+)\s*=\s*(.*?)\s*$", re.MULTILINE)
_TEMPLATE_RE = re.compile(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]")
_STRIP_RE = re.compile(r"\{\{[^{}]*\}\}|\[\[(?:[^|\]]*\|)?")
_UNLICENSED_NOTICE_RE = re.compile(r"fair use|non-free|copyright", re.IGNORECASE)


@dataclass(frozen=True)
class MapInfo:
    """Structured fields extracted from a map's infobox."""

    name: str
    description: str
    terrain: str
    size: str
    image_file: str
    image_license: str
    source_url: str


@dataclass(frozen=True)
class MapData:
    """A map descriptor ready for storage: info + resolved image URL."""

    info: MapInfo
    image_url: str
    attribution: str = LIQUIPEDIA_ATTRIBUTION


class MapLicenseError(RuntimeError):
    """The map's image carries an incompatible upstream licence."""


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
        self._last_request_at = 0.0
        self._last_parse_at = 0.0
        self._cache: dict[str, MapData] = {}

    # -- transport ---------------------------------------------------------

    def _rate_limit(self, is_parse: bool) -> None:
        """Sleep until the request is allowed by the API terms of use."""
        now = time.monotonic()
        interval = _PARSE_INTERVAL_S if is_parse else _MIN_REQUEST_INTERVAL_S
        last = self._last_parse_at if is_parse else self._last_request_at
        wait = last + interval - now
        if wait > 0:
            self._sleep(wait)
        marker = time.monotonic()
        if is_parse:
            self._last_parse_at = marker
        else:
            self._last_request_at = marker

    def _get_json(self, params: dict[str, str], is_parse: bool = False) -> dict[str, Any]:
        self._rate_limit(is_parse=is_parse)
        response = self._client.get(self._api_url, params=params)
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        if "error" in payload:
            raise RuntimeError(f"Liquipedia API error: {payload['error']}")
        return payload

    # -- API ---------------------------------------------------------------

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
        return MapData(info=info, image_url=image_url)

    def _fetch_infobox(self, page: str) -> MapInfo:
        payload = self._get_json(
            {
                "action": "parse",
                "page": page,
                "prop": "wikitext",
                "section": "0",
                "format": "json",
                "formatversion": "2",
            },
            is_parse=True,
        )
        wikitext = str(payload.get("parse", {}).get("wikitext", ""))
        fields = self._parse_infobox_fields(wikitext)
        name = fields.get("name") or page.replace("_", " ")
        description = self._clean_wikitext(fields.get("description", "")) or (
            f"Map page on Liquipedia: {LIQUIPEDIA_ATTRIBUTION}{page}"
        )
        return MapInfo(
            name=name,
            description=description,
            terrain=self._clean_wikitext(fields.get("terrain", "")),
            size=self._clean_wikitext(fields.get("size", "")),
            image_file=fields.get("image", ""),
            image_license="",
            source_url=f"{LIQUIPEDIA_ATTRIBUTION}{page}",
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
