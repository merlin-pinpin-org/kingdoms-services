"""Unit tests for the Liquipedia maps provider (kingdoms-services#247).

Hermetic: every HTTP request hits an in-memory httpx mock transport
(the repo's network_free autouse fixture fails any real socket connect),
and the rate limiter is neutralized so the suite stays fast while still
asserting that pacing happens.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from kingdoms.mapsdata import (
    LiquipediaMapsProvider,
    MapLicenseError,
)

API_URL = "https://liquipedia.test/api.php"

USER_AGENT = "KingdomsBot/1.0 (https://github.com/merlin-pinpin-org; dev@example.com)"


def _wiki_transport(  # type: ignore[no-untyped-def]
    handler,
):
    return httpx.MockTransport(handler)


def _mock_transport(
    parse_wikitext: str,
    image_url: str = "https://liquipedia.test/images/dry_river.png",
    license_short_name: str = "CC-BY-SA 3.0",
    rate_limiter=None,  # type: ignore[no-untyped-def]
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        body: dict[str, Any]
        if params.get("action") == "parse":
            body = {"parse": {"wikitext": parse_wikitext}}
        else:
            pages = [
                {
                    "title": params["titles"],
                    "imageinfo": (
                        [{"url": image_url}]
                        if params.get("iiprop") == "url"
                        else [{"extmetadata": {"LicenseShortName": {"value": license_short_name}}}]
                    ),
                }
            ]
            body = {"query": {"pages": pages}}
        return httpx.Response(200, text=json.dumps(body))

    return httpx.MockTransport(handler)


def _make_provider(transport: httpx.MockTransport) -> LiquipediaMapsProvider:
    provider = LiquipediaMapsProvider(USER_AGENT, api_url=API_URL, sleep=lambda _s: None)
    provider._client = httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"},
        transport=transport,
    )
    return provider


DRY_RIVER_WIKITEXT = """
{{Infobox map
|name=Dry river
|description=Arid riverbed crossing
|terrain=Arid
|size=2 players
|image=Dry_river.jpg
}}
"""


def test_user_agent_is_mandatory() -> None:
    """A generic/empty UA is refused: Liquipedia blocks those."""
    with pytest.raises(ValueError, match="User-Agent"):
        LiquipediaMapsProvider("", api_url=API_URL)
    with pytest.raises(ValueError, match="User-Agent"):
        LiquipediaMapsProvider("python-requests/2.0", api_url=API_URL)


def test_map_data_fetches_info_and_image() -> None:
    """A map page yields structured info + resolved image URL."""
    provider = _make_provider(_mock_transport(DRY_RIVER_WIKITEXT))
    data = provider.map_data("Dry River")
    assert data.info.name == "Dry river"
    assert data.info.description == "Arid riverbed crossing"
    assert data.info.terrain == "Arid"
    assert data.info.size == "2 players"
    assert data.info.source_url == "https://liquipedia.net/ageofempires/Dry_River"
    assert data.image_url.endswith("dry_river.png")
    assert data.attribution == "https://liquipedia.net/ageofempires/"
    provider.close()


def test_map_data_is_cached_per_page() -> None:
    """A second fetch of the same map is served from cache: no request."""
    transport = _mock_transport(DRY_RIVER_WIKITEXT)
    provider = _make_provider(transport)
    first = provider.map_data("Dry River")
    calls = []

    original = provider._get_json

    def counting_get_json(params: dict[str, str], is_parse: bool = False) -> dict[str, Any]:
        calls.append(params)
        return original(params, is_parse=is_parse)

    provider._get_json = counting_get_json  # type: ignore[method-assign]
    second = provider.map_data("Dry River")
    assert second == first
    assert calls == []
    provider.close()


def test_incompatible_image_license_is_refused() -> None:
    """A non-free image (fair use / © notice) is never imported."""
    provider = _make_provider(
        _mock_transport(
            DRY_RIVER_WIKITEXT,
            license_short_name="Non-free video game screenshot",
        )
    )
    with pytest.raises(MapLicenseError, match="licence"):
        provider.map_data("Dry River")
    provider.close()


def test_rate_limit_paces_requests() -> None:
    """Requests are serialized: a parse right after another waits."""
    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    provider = LiquipediaMapsProvider(USER_AGENT, api_url=API_URL, sleep=fake_sleep)
    provider._client = httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"},
        transport=_mock_transport(DRY_RIVER_WIKITEXT),
    )
    provider.map_data("Dry River")
    assert sleeps and all(wait >= 1.5 for wait in sleeps)
    provider.close()


def test_unknown_page_raises() -> None:
    """A missing page is an API error, surfaced to the caller."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=json.dumps({"error": {"code": "missingtitle", "info": "The page you specified doesn't exist."}}),
        )

    provider = _make_provider(httpx.MockTransport(handler))
    with pytest.raises(RuntimeError, match="missingtitle"):
        provider.map_data("Unknown Map")
    provider.close()
