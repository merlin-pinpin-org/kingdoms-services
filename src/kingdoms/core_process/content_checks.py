"""External-services battery for the content providers (aoe2techtree).

Hits the deployed ext process when ``EXT_AOE2TECHTREE_URI`` is set (the
CI compose runs it), else falls back to the dataset source directly.
Each check is a *real* access + mapping verification: the faction list
must be non-trivial, a known civ must resolve in fr AND en, and the
provider mapping must round-trip.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from kingdoms.core_process.external_checks import CheckResult, _run_check, register_battery

PROVIDER = "aoe2techtree"
KNOWN_FACTIONS = ("Aztecs", "Franks", "Britons")
EXPECTED_MIN_FACTIONS = 40


def _build_source() -> object:
    """Resolve the upstream under test: RPC first, dataset fallback."""
    uri = os.environ.get("EXT_AOE2TECHTREE_URI", "").strip()
    if uri:
        from kingdoms.core.rpc.content_client import ContentProviderClient

        return ContentProviderClient(uri)
    from kingdoms.ext_aoe2techtree.source import resolve_source

    return resolve_source(os.environ.get("AOE2TECHTREE_SOURCE", ""))


def _call(source: object, name: str, *args: object) -> Any:
    """Call a sync or async source method and return its result."""
    method = getattr(source, name, None)
    if method is None:
        fallback = {
            "list_factions": "faction_keys",
            "get_faction_content": "faction_content",
            "get_map_content": "map_content",
        }[name]
        method = getattr(source, fallback)
    result = method(*args)
    if asyncio.iscoroutine(result):
        return asyncio.run(result)
    return result


async def _check_faction_index() -> None:
    source = _build_source()
    raw_keys = _call(source, "list_factions") or []
    keys = [str(k) for k in raw_keys]
    if len(keys) < EXPECTED_MIN_FACTIONS:
        raise AssertionError(f"faction index too small ({len(keys)} < {EXPECTED_MIN_FACTIONS})")
    for known in KNOWN_FACTIONS:
        if known not in keys:
            raise AssertionError(f"known faction {known!r} missing from the index ({len(keys)} keys)")


async def _check_faction_content_fr_en() -> None:
    source = _build_source()
    for locale in ("fr", "en"):
        content = _call(source, "get_faction_content", "Franks", locale)
        if not content:
            raise AssertionError(f"Franks content missing in {locale}")
        name = str(content.get("name", ""))
        if not name:
            raise AssertionError(f"Franks name empty in {locale}: {content!r}")
        if locale == "fr" and name == "Franks":
            raise AssertionError(f"Franks name not localized in fr: {content!r}")
        if not content.get("source_url"):
            raise AssertionError(f"Franks descriptor lacks source_url in {locale}")


async def _check_map_content() -> None:
    source = _build_source()
    content = _call(source, "get_map_content", "arabia", "en")
    if not content:
        raise AssertionError("map content for arabia missing")
    if str(content.get("name", "")).lower() != "arabia":
        raise AssertionError(f"map name mapping broken for arabia: {content!r}")


async def battery() -> list[CheckResult]:
    """Run every content check; all of them, whatever happens."""
    return [
        await _run_check(PROVIDER, "faction-index", _check_faction_index),
        await _run_check(PROVIDER, "faction-content-fr-en", _check_faction_content_fr_en),
        await _run_check(PROVIDER, "map-content-mapping", _check_map_content),
    ]


register_battery(PROVIDER, battery)
