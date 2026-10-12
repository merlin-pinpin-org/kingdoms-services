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


ARABIA_WIKITEXT = """{{tabs static
|name1=Arabia (AoE2)|link1=Arabia|name2=Arabia (AoE4)|link2=Arabia (AoE4)|This=1}}
{{Infobox map
|name=Arabia
|description=The classic open map
|terrain=Land
|type=Land
|image=Arabia.png
}}
'''Arabia''' is a [[Random Map]] in [[Age of Empires II]].
==Versions==
<!--''list of the major official changes.'' -->
*'''Arabia (default)''': standard Arabia map with big variety; can spawn with grass or desert terrain
**Grass terrain: trees, more "noob friendly" with deer/sheep/boar and resources being closer to the [[Town Center|TC]]
**Desert terrain: palms, ponds, less wood in general
*'''Desert Arabia''': an Arabia map always spawning with (brown) desert terrain
*[[Escape Champions League|ECL_Arabia]] (also used for [[EscapeTV Launch Event]])
*RBW_Arabia ([[Red_Bull_Wololo|event example]])
==Recent Tournament Appearances==
{{Map appearances table|maps=Arabia}}
"""


def test_map_data_parses_game_tabs_and_versions() -> None:
    """The full-page parse yields the game tabs and the map variants."""
    provider = _make_provider(_mock_transport(ARABIA_WIKITEXT))
    data = provider.map_data("Arabia")
    assert data.info.game_versions == ("Arabia (AoE2)", "Arabia (AoE4)")
    names = [v.name for v in data.info.versions]
    assert names == [
        "Arabia (default)",
        "Desert Arabia",
        "ECL_Arabia (also used for EscapeTV Launch Event)",
        "RBW_Arabia (event example)",
    ]
    default = data.info.versions[0]
    assert default.description.startswith("standard Arabia map")
    assert default.details == (
        'Grass terrain: trees, more "noob friendly" with deer/sheep/boar '
        "and resources being closer to the TC",
        "Desert terrain: palms, ponds, less wood in general",
    )
    provider.close()


def test_versions_absent_section_yields_no_variants() -> None:
    """A page without a Versions section carries no variants."""
    provider = _make_provider(_mock_transport(DRY_RIVER_WIKITEXT))
    data = provider.map_data("Dry River")
    assert data.info.versions == ()
    provider.close()


def test_query_tags_normalize_info() -> None:
    """Tags are stable, lowercase, and water derives from hybrid/water."""
    from kingdoms.mapsdata.liquipedia import query_tags

    provider = _make_provider(_mock_transport(ARABIA_WIKITEXT))
    info = provider.map_data("Arabia").info
    assert query_tags(info) == ("terrain:land",)  # type deduped against terrain
    hybrid = type(info)(
        name="X", description="", terrain="Hybrid", size="",
        map_type="", image_file="", image_license="", source_url="",
    )
    assert "water" in query_tags(hybrid)
    provider = _make_provider(_mock_transport(ARABIA_WIKITEXT))
    provider.close()
