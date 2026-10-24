"""Provider endpoints: the config-backed source of truth.

A provider's URL changing means its API changed — the endpoints live in
``config/services.yaml``, versioned and reviewed with the code that
speaks each protocol (developer direction, 2026-10-22). This module owns
the typed access:

- ``provider_url(provider, key)`` — one endpoint, with the env-var
  override (``AOE2LOBBY_WS_URL``...) as the escape hatch for
  out-of-band experiments;
- ``provider_urls(provider)`` — the provider's full endpoint block.

The adapters consume this and never carry their own URL literals: an
URL change is a config change riding the same PR as the protocol
change it implies.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

_DEFAULT_CONFIG = Path(__file__).resolve().parents[4] / "config" / "services.yaml"

_ENV_OVERRIDE = {
    ("aoe2lobby", "ws_url"): "AOE2LOBBY_WS_URL",
    ("aoe2lobby", "schema_ws_url"): "AOE2LOBBY_SCHEMA_WS_URL",
    ("librematch", "api_url"): "LIBREMATCH_API_URL",
}

_cache: dict[str, Any] | None = None


def _load() -> dict[str, Any]:
    """Load the provider endpoints (cached per process)."""
    global _cache
    if _cache is None:
        try:
            _cache = yaml.safe_load(_DEFAULT_CONFIG.read_text()) or {}
        except Exception:
            _cache = {}
    return _cache


def provider_urls(provider: str) -> dict[str, str]:
    """Return one provider's endpoint block (empty when undeclared)."""
    providers = _load().get("providers") or {}
    block = providers.get(provider)
    if not isinstance(block, dict):
        return {}
    return {str(k): str(v) for k, v in block.items() if isinstance(v, str)}


def provider_url(provider: str, key: str) -> str:
    """Resolve one endpoint: the env override, else the declared URL.

    The override is the escape hatch (an out-of-band experiment never
    edits the config); the declared URL is the source of truth.
    """
    override = os.environ.get(_ENV_OVERRIDE.get((provider, key), ""), "")
    if override:
        return override
    return provider_urls(provider).get(key, "")
