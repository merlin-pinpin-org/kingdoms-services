"""Unit tests for the external-services battery runner and pinned-menu update."""

from __future__ import annotations

from typing import Any

import pytest

from kingdoms.core.services.pinned_menu import PinnedMenuService
from kingdoms.core_process.external_checks import (
    CheckResult,
    register_battery,
    registered_batteries,
    render_report,
    run_all_batteries,
)


def _async_layout(value: str):
    async def _build(guild_id: str) -> str:
        return value

    return _build


@pytest.mark.asyncio
async def test_all_checks_run_despite_failures() -> None:
    """A battery reports ALL failures — it never stops at the first."""
    calls: list[str] = []

    async def failing_battery() -> list[CheckResult]:
        calls.append("failing")
        return [
            CheckResult("p", "first", ok=False, detail="boom"),
            CheckResult("p", "second", ok=True),
            CheckResult("p", "third", ok=False, detail="bang"),
        ]

    async def broken_battery() -> list[CheckResult]:
        calls.append("broken")
        raise RuntimeError("battery exploded")

    register_battery("p", failing_battery)
    register_battery("q", broken_battery)
    assert set(registered_batteries()) >= {"p", "q"}

    results = await run_all_batteries()
    assert calls == ["failing", "broken"]
    failed = {r.name for r in results if not r.ok}
    assert {"first", "third", "battery"} <= failed
    report = render_report(results)
    assert "boom" in report and "bang" in report and "battery exploded" in report


@pytest.mark.asyncio
async def test_update_current_edits_in_place() -> None:
    """A stale-revision menu keeps its id: it is edited, not re-posted."""
    edits: list[str] = []
    sent: list[Any] = []

    class _Message:
        def __init__(self, mid: str, ids: list[str]) -> None:
            self.id = mid
            self._ids = ids

        async def pin(self, reason: str) -> None:
            pass

        @property
        def components(self) -> list[Any]:
            class _C:
                custom_id = self._ids[0]

            return [_C()]

    class _Channel:
        id = "1"

        def __init__(self, ids: list[str]) -> None:
            self._ids = ids
            self.message = _Message("100", ids)

        async def pins(self) -> list[Any]:
            return [self.message]

        async def fetch_message(self, mid: int) -> Any:
            return self.message

    class _Delivery:
        async def deliver(self, channel: Any, layout: Any) -> str:
            sent.append(layout)
            return "200"

    class _Updater:
        async def update(self, channel: Any, message_id: str, layout: Any) -> bool:
            edits.append(message_id)
            return True

    channel = _Channel(["home:pin:btn"])
    service = PinnedMenuService(_Delivery(), _Updater())  # type: ignore[arg-type]
    created = await service.ensure(
        "g1",
        channel,  # type: ignore[arg-type]
        marker="home:pin:",
        build_layout=_async_layout("layout-v2"),
        pin_reason="test",
        required_ids=("home:pin:btn", "home:pin:new"),
    )
    assert created is False
    assert edits == ["100"]
    assert sent == []


@pytest.mark.asyncio
async def test_update_falls_back_to_repost() -> None:
    """A failing in-place edit re-posts the menu (self-healing)."""
    sent: list[Any] = []

    class _Message:
        id = "100"

        async def pin(self, reason: str) -> None:
            pass

        async def unpin(self, reason: str) -> None:
            pass

        @property
        def components(self) -> list[Any]:
            class _C:
                custom_id = "home:pin:btn"

            return [_C()]

    class _Channel:
        id = "1"

        async def pins(self) -> list[Any]:
            return [_Message()]

        async def fetch_message(self, mid: int) -> Any:
            raise RuntimeError("gone")

    class _Delivery:
        async def deliver(self, channel: Any, layout: Any) -> str:
            sent.append(layout)
            return "200"

    class _Updater:
        async def update(self, channel: Any, message_id: str, layout: Any) -> bool:
            return False

    service = PinnedMenuService(_Delivery(), _Updater())  # type: ignore[arg-type]
    created = await service.ensure(
        "g1",
        _Channel(),  # type: ignore[arg-type]
        marker="home:pin:",
        build_layout=_async_layout("layout-v2"),
        pin_reason="test",
        required_ids=("home:pin:btn", "home:pin:new"),
    )
    assert created is False
    assert sent == ["layout-v2"]
