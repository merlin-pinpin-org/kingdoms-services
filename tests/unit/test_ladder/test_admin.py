"""Unit tests for the ladder admin surface (kingdoms-services#141)."""

from __future__ import annotations

import pytest
from tests.unit.test_ladder.test_domain import GAME, NOW, OWNER, FakeEvents, FakeLadderDatabase, _game_data

from kingdoms.mods.ladder.admin import (
    InvalidSettingsError,
    LadderAdminService,
    LadderAlreadyExistsError,
)
from kingdoms.mods.ladder.service import LadderService, NullGameGateway


def _env() -> tuple[LadderAdminService, LadderService, list[tuple[str, dict[str, object]]]]:
    db, events, game_data = FakeLadderDatabase(), FakeEvents(), _game_data()
    svc = LadderService(db, game_data, NullGameGateway(), events)
    audit_lines: list[tuple[str, dict[str, object]]] = []

    class Audit:
        async def record(self, action: str, payload: dict[str, object]) -> None:
            audit_lines.append((action, payload))

    return LadderAdminService(svc, Audit()), svc, audit_lines


AVAILABLE = [GAME, "chess"]


@pytest.mark.asyncio
async def test_create_ladder_with_validation_and_audit() -> None:
    admin, _, audit = _env()
    ladder = await admin.create_ladder(OWNER, "Ladder 1v1", GAME, AVAILABLE, now=NOW)
    assert ladder.name == "Ladder 1v1"
    assert ("ladder.create", {"owner_ref": OWNER, "name": "Ladder 1v1", "game_key": GAME}) in audit


@pytest.mark.asyncio
async def test_mono_guild_ladder_enforced() -> None:
    admin, _, _ = _env()
    await admin.create_ladder(OWNER, "L1", GAME, AVAILABLE, now=NOW)
    with pytest.raises(LadderAlreadyExistsError, match="already has a ladder"):
        await admin.create_ladder(OWNER, "L2", GAME, AVAILABLE, now=NOW)
    with pytest.raises(LadderAlreadyExistsError, match="already has a ladder"):
        await admin.create_ladder(OWNER, "L2", "chess", AVAILABLE, now=NOW)


@pytest.mark.asyncio
async def test_create_rejects_unavailable_game_and_empty_name() -> None:
    admin, _, _ = _env()
    with pytest.raises(ValueError, match="not available"):
        await admin.create_ladder(OWNER, "L1", "starcraft", AVAILABLE, now=NOW)
    with pytest.raises(ValueError, match="must not be empty"):
        await admin.create_ladder(OWNER, "   ", GAME, AVAILABLE, now=NOW)


@pytest.mark.asyncio
async def test_update_settings_bounds_enforced() -> None:
    admin, svc, _ = _env()
    ladder = await admin.create_ladder(OWNER, "L1", GAME, AVAILABLE, now=NOW)
    updated = await admin.update_settings(ladder.id, "admin:1", {"ready_timeout": 300})
    assert updated.settings.ready_timeout == 300
    with pytest.raises(InvalidSettingsError, match="ready_timeout"):
        await admin.update_settings(ladder.id, "admin:1", {"ready_timeout": 0})
    with pytest.raises(InvalidSettingsError, match="unknown setting"):
        await admin.update_settings(ladder.id, "admin:1", {"hack": 1})
    del svc


@pytest.mark.asyncio
async def test_update_settings_consistency_rules() -> None:
    admin, _, _ = _env()
    ladder = await admin.create_ladder(OWNER, "L1", GAME, AVAILABLE, now=NOW)
    with pytest.raises(InvalidSettingsError, match="elo_floor"):
        await admin.update_settings(ladder.id, "admin:1", {"elo_floor": 4000})
    with pytest.raises(InvalidSettingsError, match="elo_k_standard"):
        await admin.update_settings(ladder.id, "admin:1", {"elo_k_standard": 120})
    with pytest.raises(InvalidSettingsError, match="base_elo_threshold"):
        await admin.update_settings(ladder.id, "admin:1", {"base_elo_threshold": 1500})


@pytest.mark.asyncio
async def test_settings_update_audited() -> None:
    admin, _, audit = _env()
    ladder = await admin.create_ladder(OWNER, "L1", GAME, AVAILABLE, now=NOW)
    await admin.update_settings(ladder.id, "admin:1", {"elo_initial": 1200})
    assert any(a == "ladder.settings.update" for a, _ in audit)
