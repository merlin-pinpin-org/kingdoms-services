"""Unit tests for the Liquipedia maps provider (kingdoms-services#247).

Hermetic: every HTTP request hits an in-memory httpx mock transport
(the repo's network_free autouse fixture fails any real socket connect),
and the rate limiter is neutralized so the suite stays fast while still
asserting that pacing happens.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

import httpx
import pytest

from kingdoms.ext_liquipedia.liquipedia import (
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
    from kingdoms.ext_liquipedia.liquipedia import query_tags

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


def test_pacing_is_shared_across_provider_instances(monkeypatch: pytest.MonkeyPatch) -> None:
    """One pacing clock per process — a fresh provider per map still waits.

    fetch_map_seed builds a new provider per map: with instance-level
    markers every map's first parse fired immediately and the batch
    hammered the API into 429s. The markers are module-level: a second
    provider paces against the first one's last parse.
    """
    import kingdoms.ext_liquipedia.liquipedia as lp

    sleeps: list[float] = []
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url.params.get("page")))
        body = {"parse": {"wikitext": "{{Infobox map\n|name=X\n}}"}}
        return httpx.Response(200, text=json.dumps(body))

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    # reset the shared markers so the test is order-independent
    monkeypatch.setattr(lp, "_shared_last_request_at", 0.0)
    monkeypatch.setattr(lp, "_shared_last_parse_at", 0.0)

    first = lp.LiquipediaMapsProvider(USER_AGENT, api_url=API_URL, sleep=fake_sleep)
    first._client = httpx.Client(
        headers={"User-Agent": USER_AGENT}, transport=httpx.MockTransport(handler)
    )
    second = lp.LiquipediaMapsProvider(USER_AGENT, api_url=API_URL, sleep=fake_sleep)
    second._client = httpx.Client(
        headers={"User-Agent": USER_AGENT}, transport=httpx.MockTransport(handler)
    )

    # neutralize the sub-parse pacing (image queries) for the assertion
    first.map_data("Arabia")
    first_sleeps = list(sleeps)
    second.map_data("Black Forest")
    # the second provider's first parse waited the full 30 s interval
    # minus whatever the first provider's remaining pacing used.
    assert sleeps[len(first_sleeps) :], "the second provider slept"
    waits = sleeps[len(first_sleeps) :]
    assert max(waits) > 0, "a fresh provider paced against the shared clock"
    assert len(calls) >= 2
    first.close()
    second.close()


def test_429_honors_retry_after_then_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 429 is 'later', not 'no such page' — retry once after Retry-After."""
    import kingdoms.ext_liquipedia.liquipedia as lp

    attempts: list[int] = []
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(429, headers={"Retry-After": "1"})
        return httpx.Response(
            200, text=json.dumps({"parse": {"wikitext": "{{Infobox map\n|name=X\n}}"}})
        )

    monkeypatch.setattr(lp, "_shared_last_request_at", 0.0)
    monkeypatch.setattr(lp, "_shared_last_parse_at", 0.0)

    provider = lp.LiquipediaMapsProvider(USER_AGENT, api_url=API_URL, sleep=sleeps.append)
    provider._client = httpx.Client(
        headers={"User-Agent": USER_AGENT}, transport=httpx.MockTransport(handler)
    )
    data = provider.map_data("Arabia")
    assert data.info.name == "X"
    # the backoff FLOOR wins over a tiny Retry-After: 30 s (attempt 1)
    assert 30.0 in sleeps, "the exponential backoff floor was honoured"
    assert len(attempts) == 2, "exactly one retry"
    provider.close()


@pytest.mark.asyncio

def test_map_data_batch_resolves_pages_and_images_in_few_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The batch path: one wikitext query + two imageinfo queries, 2 s pacing.

    action=parse is capped at one request per 30 s by the API terms;
    the batch rides action=query (2 s pacing) and carries up to 50
    pages per call \u2014 a whole catalog in a handful of requests.
    """
    import kingdoms.ext_liquipedia.liquipedia as lp

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        titles = str(params.get("titles") or "")
        calls.append(f"{params.get('action')}:{params.get('prop')}")
        if params.get("prop") == "revisions":
            pages = []
            for title in titles.split("|"):
                if title == "Missing_Map":
                    pages.append({"title": title, "missing": True})
                    continue
                pages.append(
                    {
                        "title": title,
                        "revisions": [
                            {
                                "slots": {
                                    "main": {
                                        "content": (
                                            f"{{{{Infobox map\n|name={title}\n|image={title}.png\n}}}}"
                                        )
                                    }
                                }
                            }
                        ],
                    }
                )
            return httpx.Response(200, text=json.dumps({"query": {"pages": pages}}))
        # imageinfo
        assert params.get("prop") == "imageinfo"
        iiprop = str(params.get("iiprop"))
        pages = []
        for title in titles.split("|"):
            if "missing" in title:
                pages.append({"title": title, "missing": True})
                continue
            if iiprop == "url":
                imageinfo = [{"url": f"https://liquipedia.test/images/{title}"}]
            else:
                imageinfo = [
                    {"extmetadata": {"LicenseShortName": {"value": "CC-BY-SA 3.0"}}}
                ]
            pages.append({"title": title, "imageinfo": imageinfo})
        return httpx.Response(200, text=json.dumps({"query": {"pages": pages}}))

    def fake_sleep(seconds: float) -> None:
        pass

    monkeypatch.setattr(lp, "_shared_last_request_at", 0.0)
    monkeypatch.setattr(lp, "_shared_last_parse_at", 0.0)

    provider = lp.LiquipediaMapsProvider(USER_AGENT, api_url=API_URL, sleep=fake_sleep)
    provider._client = httpx.Client(
        headers={"User-Agent": USER_AGENT}, transport=httpx.MockTransport(handler)
    )
    data = provider.map_data_batch(["Arabia", "Black Forest", "Missing Map"])
    # the two existing pages are resolved with their images; the missing
    # page is simply absent.
    assert set(data) == {"Arabia", "Black_Forest"}
    assert data["Arabia"].image_url.startswith("https://liquipedia.test/images/")
    assert data["Arabia"].info.name == "Arabia"
    # one revisions query + two imageinfo queries \u2014 no action=parse at all.
    assert calls.count("query:revisions") == 1
    assert calls.count("query:imageinfo") == 2
    assert "parse:wikitext" not in calls
    # the second batch call serves from cache: zero extra requests.
    calls.clear()
    again = provider.map_data_batch(["Arabia", "Black Forest"])
    assert set(again) == {"Arabia", "Black_Forest"}
    assert calls == []
    provider.close()


def test_liquipedia_circuit_opens_after_exhausted_429s() -> None:
    """Exhausted 429 retries OPEN the circuit: every further call fails
    fast, zero HTTP traffic, until the window lapses."""

    from kingdoms.ext_liquipedia import liquipedia as lp

    class _Always429:
        status_code = 429
        headers: ClassVar[dict] = {"Retry-After": "0"}

        def raise_for_status(self) -> None:
            raise httpx.HTTPStatusError(
                "429 Too Many Requests",
                request=httpx.Request("GET", "https://liquipedia.net/api.php"),
                response=httpx.Response(429),
            )

    class _Client:
        headers: ClassVar[dict] = {}

        def __init__(self) -> None:
            self.calls = 0

        def get(self, url: str, params: dict | None = None) -> Any:
            self.calls += 1
            return _Always429()

    provider = lp.LiquipediaMapsProvider(user_agent="test <t@example>")
    provider._client = _Client()  # type: ignore[attr-defined]
    provider._sleep = lambda s: None  # type: ignore[method-assign]
    lp._shared_circuit_open_until = 0.0
    lp._shared_circuit_streak = 0
    try:
        with pytest.raises(httpx.HTTPStatusError):
            provider._get_json({"action": "query"})
        # the circuit is OPEN now
        assert lp.LiquipediaCircuitOpenError.retry_in_s() > 0
        # a fail-fast call makes ZERO HTTP requests
        calls_before = provider._client.calls  # type: ignore[attr-defined]
        with pytest.raises(lp.LiquipediaCircuitOpenError):
            provider._get_json({"action": "query"})
        assert provider._client.calls == calls_before  # type: ignore[attr-defined]
        # the window doubles on consecutive openings
        first_window = lp.LiquipediaCircuitOpenError.retry_in_s()
        lp._shared_circuit_open_until = 0.0  # simulate the window lapsing
        with pytest.raises(httpx.HTTPStatusError):
            provider._get_json({"action": "query"})
        assert lp.LiquipediaCircuitOpenError.retry_in_s() > first_window
    finally:
        lp._shared_circuit_open_until = 0.0
        lp._shared_circuit_streak = 0


def test_liquipedia_circuit_success_resets_the_streak() -> None:

    from kingdoms.ext_liquipedia import liquipedia as lp

    class _Ok:
        status_code = 200
        headers: ClassVar[dict] = {}

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {}

    class _Client:
        headers: ClassVar[dict] = {}

        def get(self, url: str, params: dict | None = None) -> Any:
            return _Ok()

    provider = lp.LiquipediaMapsProvider(user_agent="test <t@example>")
    provider._client = _Client()  # type: ignore[attr-defined]
    provider._sleep = lambda s: None  # type: ignore[method-assign]
    lp._shared_circuit_open_until = 0.0
    lp._shared_circuit_streak = 2  # a streak from earlier openings
    try:
        provider._get_json({"action": "query"})
        assert lp._shared_circuit_streak == 0  # a healthy call resets it
    finally:
        lp._shared_circuit_open_until = 0.0
        lp._shared_circuit_streak = 0
