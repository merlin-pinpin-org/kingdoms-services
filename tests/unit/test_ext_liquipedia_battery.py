"""Liquipedia external-services battery — unit tests (mocked upstream).

The battery's contract: it SKIPS (never fails) when PROVIDER_CONTACT
is unset, and validates the batched query shape — name, source_url,
resolved image — against a stubbed provider when the contact is there.
The real upstream access only happens in CI's external-services job.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from kingdoms.core_process.external_checks import render_report
from kingdoms.core_process.mapsdata_checks import PROVIDER, battery


def _stub_maps_module(monkeypatch: pytest.MonkeyPatch, maps: dict[str, Any]) -> None:
    """Replace the real provider with one returning the given maps."""
    import kingdoms.ext_liquipedia.liquipedia as lp

    class _StubProvider:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            del args, kwargs

        def map_data_batch(self, page_names: list[str]) -> dict[str, Any]:
            del page_names
            return maps

    monkeypatch.setattr(lp, "LiquipediaMapsProvider", _StubProvider)


async def test_battery_skips_without_contact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PROVIDER_CONTACT", raising=False)
    results = await battery()
    assert len(results) == 1
    result = results[0]
    assert result.provider == PROVIDER
    assert result.skipped is True
    assert result.ok is True  # a skip never fails the CI
    assert "PROVIDER_CONTACT" in result.detail


async def test_battery_validates_query_shape_with_contact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROVIDER_CONTACT", "dev@example.com")
    maps = {
        "Arabia": SimpleNamespace(
            info=SimpleNamespace(
                name="Arabia",
                source_url="https://liquipedia.net/ageofempires/Arabia",
            ),
            image_url="https://liquipedia.net/ageofempires/images/Arabia.png",
        )
    }
    _stub_maps_module(monkeypatch, maps)
    results = await battery()
    assert results[0].ok is True
    assert results[0].skipped is False


async def test_battery_fails_when_known_map_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROVIDER_CONTACT", "dev@example.com")
    _stub_maps_module(monkeypatch, {})  # upstream drift: nothing resolves
    results = await battery()
    assert results[0].ok is False
    assert results[0].skipped is False
    assert "Arabia" in results[0].detail


def test_report_renders_skipped_separately() -> None:
    from kingdoms.core_process.external_checks import CheckResult

    report = render_report(
        [
            CheckResult("p", "fine", ok=True),
            CheckResult(PROVIDER, "maps-query-shape", ok=True, skipped=True, detail="PROVIDER_CONTACT unset"),
            CheckResult("p", "broken", ok=False, detail="boom"),
        ]
    )
    assert "[SKIP]" in report
    assert "1 skipped" in report
    assert "[PASS]" in report and "[FAIL]" in report
