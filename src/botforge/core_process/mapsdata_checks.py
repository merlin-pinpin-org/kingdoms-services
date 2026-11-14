"""External-services battery for the Liquipedia maps provider.

Liquipedia is a ToS-constrained upstream (custom identifying
User-Agent with contact information, 2 s between light requests):
the battery runs the REAL batched ``action=query`` path — the very
one the maps sync uses — on ONE well-known page, in ONE
paced batch (a handful of light requests, never a crawl).

The battery needs ``PROVIDER_CONTACT`` (the generic provider
contact — the same variable the runtime uses); without it the check
SKIPS with a clear detail
instead of failing — an unconfigured org must not see a red CI, and
Liquipedia must never see an unidentified User-Agent.
"""

from __future__ import annotations

from botforge.core_process.external_checks import CheckResult, _run_check, register_battery

PROVIDER = "liquipedia"
# ONE page only (#397): the shape check needs one representative
# payload — two titles double the throttle risk on every CI run for
# no extra signal (the batch path itself is covered by the unit tests)
KNOWN_PAGES = ("Arabia",)
KNOWN_PRESENT = "Arabia"
SKIP_DETAIL = "PROVIDER_CONTACT unset — set the repo variable to enable the battery"


def _contact() -> str:
    from botforge.core.upstream_identity import provider_contact

    return provider_contact()


async def _check_maps_query_shape() -> None:
    from botforge.ext_liquipedia.liquipedia import LiquipediaMapsProvider

    user_agent = f"botforge-bot/1.0 (https://github.com/jj-guild/botforge; {_contact()})"
    provider = LiquipediaMapsProvider(user_agent=user_agent)
    maps = provider.map_data_batch(list(KNOWN_PAGES))
    if KNOWN_PRESENT not in maps:
        raise AssertionError(f"known map {KNOWN_PRESENT!r} missing from the batch result (got {sorted(maps)})")
    arabia = maps[KNOWN_PRESENT]
    name = str(getattr(arabia.info, "name", "")).strip()
    if not name:
        raise AssertionError(f"{KNOWN_PRESENT} infobox name is empty: {arabia!r}")
    source_url = str(getattr(arabia.info, "source_url", "")).strip()
    if "liquipedia.net" not in source_url:
        raise AssertionError(f"{KNOWN_PRESENT} source_url missing or unexpected: {source_url!r}")
    image_url = str(getattr(arabia, "image_url", "")).strip()
    if not image_url:
        raise AssertionError(f"{KNOWN_PRESENT} image URL not resolved (licence metadata upstream?)")


async def battery() -> list[CheckResult]:
    """Run the liquipedia checks; all of them, whatever happens."""
    if not _contact():
        return [
            CheckResult(
                PROVIDER,
                "maps-query-shape",
                ok=True,
                skipped=True,
                detail=SKIP_DETAIL,
            )
        ]
    return [
        await _run_check(PROVIDER, "maps-query-shape", _check_maps_query_shape),
    ]


register_battery(PROVIDER, battery)
