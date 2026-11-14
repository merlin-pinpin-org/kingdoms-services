"""The SDK's beta phase tests — the state, the registry (owner direction 2026-11-14)."""

from __future__ import annotations

from typing import Any

import pytest

from botforge.core.services.beta import (
    BetaAction,
    beta_action,
    enter_beta,
    exit_beta,
    is_beta,
    register_beta_action,
    registered_beta_actions,
)


class MemoryCache:
    """The ModStateCache shape, in memory."""

    def __init__(self) -> None:
        self._entries: dict[str, dict[str, Any]] = {}

    async def get(self, key: str) -> dict[str, Any] | None:
        return self._entries.get(key)

    async def set(self, key: str, value: dict[str, Any], ttl: int | None = None) -> bool:
        self._entries[key] = value
        return True


@pytest.fixture(autouse=True)
def _clean_registry() -> None:
    from botforge.core.services import beta as beta_module

    beta_module._BETA_ACTIONS.clear()
    beta_module._MODS_WITH_ACTIONS.clear()


async def test_the_phase_bends_and_straightens() -> None:
    cache = MemoryCache()
    assert await is_beta(cache, "g1", "kingdom") is False
    await enter_beta(cache, "g1", "kingdom")
    assert await is_beta(cache, "g1", "kingdom") is True
    await exit_beta(cache, "g1", "kingdom")
    assert await is_beta(cache, "g1", "kingdom") is False


async def test_the_phase_is_per_guild() -> None:
    cache = MemoryCache()
    await enter_beta(cache, "g1", "kingdom")
    assert await is_beta(cache, "g2", "kingdom") is False


async def test_a_missing_state_service_never_bends_the_rules() -> None:
    assert await is_beta(None, "g1", "kingdom") is False
    await enter_beta(None, "g1", "kingdom")  # degrades quietly
    assert await is_beta(None, "g1", "kingdom") is False


async def test_the_registry_is_extensible_per_mod() -> None:
    async def twist(bot: Any, guild_id: str) -> str:
        return "twisted"

    action = register_beta_action("kingdom", BetaAction(key="time", label_key="ui.beta.time", handler=twist))
    register_beta_action("kingdom", BetaAction(key="distributor", label_key="ui.beta.dist", handler=twist))
    assert [a.key for a in registered_beta_actions("kingdom")] == ["time", "distributor"]
    assert beta_action("kingdom", "time") is action
    assert beta_action("kingdom", "missing") is None
    assert registered_beta_actions("other-mod") == ()


async def test_the_idempotent_registration_keeps_the_order() -> None:
    async def twist(bot: Any, guild_id: str) -> str:
        return "ok"

    first = register_beta_action("kingdom", BetaAction(key="a", label_key="ui.beta.a", handler=twist))
    again = register_beta_action("kingdom", BetaAction(key="a", label_key="ui.beta.a2", handler=twist))
    assert [a.key for a in registered_beta_actions("kingdom")] == ["a"]
    assert beta_action("kingdom", "a") is again or beta_action("kingdom", "a") is first
