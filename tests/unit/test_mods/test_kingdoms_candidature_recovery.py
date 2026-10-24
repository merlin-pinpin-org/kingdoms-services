"""Candidature recovery & loud unwired guards (Drasah's 2026-10-24 incident).

The live incident: the kingdoms mod registered with its Mongo service
stack unwired — panels and buttons still worked, so a candidature
approval "succeeded" (role assigned) while the enrollment, the welcome
DM and the King kingdom-request DM silently no-opped. Guards:

- ``ensure_kingdoms_services`` rebuilds the service stack on demand and
  never retries a failed rebuild more than once per bot;
- an approval with the service missing answers with the LOUD
  ``enroll_service_down`` note instead of pretending success;
- a validated King awaiting a kingdom may re-apply (the approval of the
  new candidature resends the kingdom-request DM) — the self-service
  recovery for a King whose DM was lost.
"""
from __future__ import annotations

from typing import Any

import pytest

import kingdoms.mods.kingdoms as kingdoms_pkg
from kingdoms.mods.kingdoms.config import default_season_config
from kingdoms.mods.kingdoms.kingdom_panels import _enrolled_blocker, _strings
from kingdoms.mods.kingdoms.kingdom_persistent import (
    KingdomsPanelWiring,
    _apply_candidature_approval,
)
from kingdoms.mods.kingdoms.service import LORD_ROLE, KingdomsService
from tests.mocks.discord_mock import MockGuild, MockInteraction
from tests.unit.test_mods.test_kingdoms_service import MemoryStore

pytestmark = pytest.mark.asyncio


def _service() -> KingdomsService:
    return KingdomsService(MemoryStore(), default_season_config())  # type: ignore[arg-type]


class _ModRolesStub:
    def __init__(self) -> None:
        self.assignments: list[tuple[str, str, str, str]] = []

    async def assign_mod_role(
        self, guild_id: str, user_id: str, mod: str, role_key: str
    ) -> None:
        self.assignments.append((guild_id, user_id, mod, role_key))


async def _noop_welcome(guild: Any, applicant_id: str, strings: Any) -> None:
    return None


async def _noop_refresh(guild: Any, locale: str) -> None:
    return None


async def test_awaiting_king_may_reapply() -> None:
    """A validated King still awaiting a kingdom is NOT blocked."""
    service = _service()
    await service.launch(["Avalon"])
    await service.enroll_king_awaiting_kingdom("p1", "Arthur")
    assert await _enrolled_blocker(_strings("fr"), service, 1) is None
    assert await _enrolled_blocker(_strings("en"), service, 1) is None


async def test_seated_lord_still_blocked() -> None:
    """A lord seated in a kingdom keeps the one-candidature rule."""
    service = _service()
    await service.launch(["Avalon"])
    await service.enroll_king_awaiting_kingdom("p1", "Arthur")
    kingdoms = await service.kingdoms()
    throne = next(k for k in kingdoms if not k.is_gaia)
    await service.claim_kingdom("p1", throne.id)
    blocker = await _enrolled_blocker(_strings("fr"), service, 1)
    assert blocker is not None and "déjà inscrit" in blocker.lower()


async def test_queued_lord_without_kingdom_still_blocked() -> None:
    """Only the KING gets the re-apply recovery, never a queued lord."""
    service = _service()
    await service.launch(["Avalon"])
    await service.enroll("p1", "Lancelot", LORD_ROLE, kingdom_name=None)
    blocker = await _enrolled_blocker(_strings("fr"), service, 1)
    assert blocker is not None


async def test_unwired_approval_is_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    """A service-down approval says so — no more silent success."""
    strings = _strings("fr")
    monkeypatch.setattr(
        "kingdoms.mods.kingdoms.kingdom_persistent._send_welcome",
        _noop_welcome,
    )
    monkeypatch.setattr(
        "kingdoms.mods.kingdoms.kingdom_persistent._refresh_season_status_safe",
        _noop_refresh,
    )
    interaction = MockInteraction(guild=MockGuild(id=777))
    roles = _ModRolesStub()
    wiring = KingdomsPanelWiring(mod_roles_service=roles)
    extra = await _apply_candidature_approval(
        interaction,
        wiring,
        strings,
        "fr",
        applicant_id="42",
        is_king=True,
        kingdom_name=None,
    )
    assert strings["enroll_service_down"] in extra
    assert roles.assignments == [("777", "42", "kingdoms", "king")]


def test_ensure_kingdoms_services_rebuilds(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing service stack is rebuilt on demand, once, loudly."""
    kingdoms_pkg._ENSURE_FAILED.clear()
    calls: list[Any] = []

    def fake_build(config: Any) -> tuple[Any, ...]:
        calls.append(config)
        return tuple(f"svc{i}" for i in range(13))

    monkeypatch.setattr(kingdoms_pkg, "_build_services", fake_build)
    bot: Any = type("Bot", (), {})()
    assert getattr(bot, "kingdoms_service", None) is None
    first = kingdoms_pkg.ensure_kingdoms_services(bot)
    assert first == "svc0"
    assert bot.kingdoms_service == "svc0"
    assert bot.kingdoms_tavern_service == "svc9"
    assert len(calls) == 1
    # The second call reads the rebuilt attribute — no rebuild storm.
    assert kingdoms_pkg.ensure_kingdoms_services(bot) == "svc0"
    assert len(calls) == 1


def test_ensure_kingdoms_services_fails_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed rebuild is logged once per bot and never hammered."""
    kingdoms_pkg._ENSURE_FAILED.clear()
    calls: list[Any] = []

    def failing_build(config: Any) -> None:
        calls.append(config)
        return None

    monkeypatch.setattr(kingdoms_pkg, "_build_services", failing_build)
    bot: Any = type("Bot", (), {})()
    assert kingdoms_pkg.ensure_kingdoms_services(bot) is None
    assert kingdoms_pkg.ensure_kingdoms_services(bot) is None
    assert len(calls) == 1


def test_tavern_import_chain_builds_services() -> None:
    """The 2026-10-24 incident: tavern.py imported NotEnrolledError from
    service.py where it does not exist — _build_services raised ImportError
    at boot and the whole kingdoms service stack degraded silently."""
    import importlib

    tavern = importlib.import_module("kingdoms.mods.kingdoms.tavern")
    assert tavern.TavernService is not None


def test_every_service_import_resolves() -> None:
    """Static scan: every name imported from kingdoms.mods.kingdoms.service
    anywhere in src/ must exist there — the ImportError class of bug (the
    2026-10-24 unwired boot) can never reach a deployed image again."""
    import pathlib
    import re

    service = pathlib.Path("src/kingdoms/mods/kingdoms/service.py").read_text()
    defined = set(re.findall(r"^(?:class|def) (\w+)", service, re.M))
    defined |= set(re.findall(r"^(\w+)(?::| =)", service, re.M))
    bad: list[str] = []
    for path in pathlib.Path("src").rglob("*.py"):
        for match in re.finditer(
            r"from kingdoms\.mods\.kingdoms\.service import ([\w, ]+)", path.read_text()
        ):
            for name in (item.strip() for item in match.group(1).split(",")):
                if name and name not in defined:
                    bad.append(f"{path}:{name}")
    assert not bad, f"unknown imports from kingdoms service: {bad}"
