"""Abstract upstream content source for the ext-aoe2techtree process.

The provider process never reads the upstream data directly: it reads it
through this seam. Two implementations ship:

- ``DatasetContentSource`` — the vendored aoe2techtree dataset (default):
  licence-clean, offline, byte-identical to the upstream HEAD.
- ``ApiContentSource`` — a remote HTTP API (``AOE2TECHTREE_API_URL``):
  a future replacement source, selected via ``AOE2TECHTREE_SOURCE=api``.

Both speak the same interface, so the gRPC contract and the core-side
consumers never change when the upstream does.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("kingdoms.ext_aoe2techtree")

DEFAULT_DATASET_DIR = Path(__file__).resolve().parents[2] / "data" / "core" / "aoe2techtree"
SUPPORTED_LOCALES = ("en", "fr")


class UpstreamContentSource(ABC):
    """One game-content upstream: localized faction/map descriptors."""

    @abstractmethod
    def faction_keys(self) -> list[str]:
        """List every faction key known to the upstream."""

    @abstractmethod
    def faction_content(self, faction_key: str, locale: str) -> dict[str, str | bool] | None:
        """Return one faction's localized descriptor, None when unknown."""

    @abstractmethod
    def map_content(self, map_key: str, locale: str) -> dict[str, str | bool] | None:
        """Return one map's localized descriptor, None when unknown."""


class DatasetContentSource(UpstreamContentSource):
    """Upstream = the vendored aoe2techtree dataset (licence-clean, offline)."""

    def __init__(self, dataset_dir: Path = DEFAULT_DATASET_DIR) -> None:
        self._dataset_dir = dataset_dir
        self._dataset: dict[str, Any] = {}
        self._strings: dict[str, dict[str, str]] = {}
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        self._dataset = json.loads((self._dataset_dir / "data.json").read_text(encoding="utf-8"))
        self._strings = {
            lng: json.loads((self._dataset_dir / f"strings-{lng}.json").read_text(encoding="utf-8"))
            for lng in SUPPORTED_LOCALES
        }
        self._loaded = True

    def faction_keys(self) -> list[str]:
        """List every civ key from the dataset's civ index."""
        self._load()
        return list((self._dataset.get("civs") or {}).keys())

    def faction_content(self, faction_key: str, locale: str) -> dict[str, str | bool] | None:
        """Resolve a civ's localized name + help text from the dataset."""
        self._load()
        civ = (self._dataset.get("civs") or {}).get(faction_key)
        if civ is None:
            return None
        table = self._strings.get(locale) or self._strings.get("en") or {}
        name = str(table.get(str(civ.get("name_string_id")), faction_key))
        summary = str(table.get(str(civ.get("help_string_id")), "")).replace("<br>", "\n").strip()
        return {
            "entity_id": f"faction:aoe2:{faction_key}",
            "locale": locale if self._strings.get(locale) else "en",
            "name": name,
            "summary": summary,
            "source_url": "https://github.com/SiegeEngineers/aoe2techtree",
            "found": True,
        }

    def map_content(self, map_key: str, locale: str) -> dict[str, str | bool] | None:
        """Serve a map's descriptor (name-only: the dataset has no map help)."""
        del locale
        return {
            "entity_id": f"map:aoe2:{map_key}",
            "locale": "en",
            "name": map_key,
            "summary": "",
            "source_url": "https://github.com/SiegeEngineers/aoe2techtree",
            "found": True,
        }


class ApiContentSource(UpstreamContentSource):
    """Upstream = a remote HTTP JSON API (replacement source, same interface).

    Expected API shape (documented for a future upstream author):
    ``GET {base}/factions`` → ``{"factions": ["Aztecs", ...]}``;
    ``GET {base}/factions/{key}?locale=fr`` → the same descriptor dict
    as the dataset source (``entity_id``, ``locale``, ``name``,
    ``summary``, ``source_url``); ``GET {base}/maps/{key}`` likewise.
    """

    def __init__(self, api_url: str, api_key: str = "", timeout_s: float = 10.0) -> None:
        self._api_url = api_url.rstrip("/")
        self._api_key = api_key
        self._timeout_s = timeout_s

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"accept": "application/json"}
        if self._api_key:
            headers["authorization"] = f"Bearer {self._api_key}"
        return headers

    def faction_keys(self) -> list[str]:
        """List the API's faction index."""
        response = httpx.get(f"{self._api_url}/factions", headers=self._headers(), timeout=self._timeout_s)
        response.raise_for_status()
        return list(response.json().get("factions", []))

    def faction_content(self, faction_key: str, locale: str) -> dict[str, str | bool] | None:
        """Fetch one faction's descriptor from the API."""
        response = httpx.get(
            f"{self._api_url}/factions/{faction_key}",
            params={"locale": locale},
            headers=self._headers(),
            timeout=self._timeout_s,
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        return {
            "entity_id": str(payload.get("entity_id", f"faction:aoe2:{faction_key}")),
            "locale": str(payload.get("locale", locale)),
            "name": str(payload.get("name", faction_key)),
            "summary": str(payload.get("summary", "")),
            "source_url": str(payload.get("source_url", self._api_url)),
            "found": True,
        }

    def map_content(self, map_key: str, locale: str) -> dict[str, str | bool] | None:
        """Fetch one map's descriptor from the API."""
        response = httpx.get(
            f"{self._api_url}/maps/{map_key}",
            params={"locale": locale},
            headers=self._headers(),
            timeout=self._timeout_s,
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        return {
            "entity_id": str(payload.get("entity_id", f"map:aoe2:{map_key}")),
            "locale": str(payload.get("locale", locale)),
            "name": str(payload.get("name", map_key)),
            "summary": str(payload.get("summary", "")),
            "source_url": str(payload.get("source_url", self._api_url)),
            "found": True,
        }


def resolve_source(
    source: str,
    dataset_dir: Path = DEFAULT_DATASET_DIR,
    api_url: str = "",
    api_key: str = "",
) -> UpstreamContentSource:
    """Select the upstream implementation from config (dataset default).

    ``dataset`` (default) reads the vendored files; ``api`` reads a remote
    HTTP API and requires ``AOE2TECHTREE_API_URL``. Any other value fails
    closed at startup — a misconfigured source must never silently serve
    nothing.
    """
    if source == "api":
        if not api_url:
            raise RuntimeError("AOE2TECHTREE_SOURCE=api requires AOE2TECHTREE_API_URL")
        return ApiContentSource(api_url, api_key=api_key)
    if source in ("", "dataset"):
        return DatasetContentSource(dataset_dir)
    raise RuntimeError(f"unknown AOE2TECHTREE_SOURCE: {source!r} (expected 'dataset' or 'api')")
