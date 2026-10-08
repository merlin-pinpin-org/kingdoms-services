"""Kingdoms 🏰 Royaume admin operations — unit tests (D75, #214 phase 1.2).

Every panel operation through the journal: the reason is mandatory,
the before-state is snapshotted, the domain rules hold (capacity gel,
one King, throne inheritance, dissolution to Gaïa), and the rollback
restores the exact prior documents — including deletions of what the
action created.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from kingdoms.mods.kingdoms.admin_journal import AdminReasonRequiredError
from kingdoms.mods.kingdoms.admin_service import (
    EjectKingError,
    FoundationWindowClosedError,
    KingdomAdminService,
    ReassignError,
    ThroneSwapError,
)
from kingdoms.mods.kingdoms.config import default_season_config
from kingdoms.mods.kingdoms.models import (
    GAIA_KINGDOM_KEY,
    LordRole,
    TerritoryModel,
)
from kingdoms.mods.kingdoms.service import (
    AlreadyEnrolledError,
    ApplicationsClosedError,
    FoundationClosedError,
    KingdomFullError,
    KingdomLimitError,
    KingdomNameInvalidError,
    KingdomNotFoundError,
    KingdomsService,
    RecruitmentClosedError,
)

from .test_kingdoms_service import MemoryStore

ACTOR = {"actor_id": "admin", "actor_name": "Drasah"}


def _admin() -> tuple[KingdomAdminService, KingdomsService, MemoryStore]:
    store = MemoryStore()
    kingdoms = KingdomsService(store, default_season_config())  # type: ignore[arg-type]
    return KingdomAdminService(kingdoms, store), kingdoms, store


async def _launched() -> tuple[KingdomAdminService, KingdomsService, MemoryStore]:
    """An imposed season: Aquitaine (k-1) and Bourgogne (k-2) exist."""
    admin, kingdoms, store = _admin()
    await kingdoms.launch(["Aquitaine", "Bourgogne"])
    return admin, kingdoms, store


async def _free() -> tuple[KingdomAdminService, KingdomsService, MemoryStore]:
    """A free-founding season: only Gaïa exists."""
    admin, kingdoms, store = _admin()
    await kingdoms.launch()
    return admin, kingdoms, store


async def _enroll_lord(kingdoms: KingdomsService, player: str, kingdom: str) -> None:
    await kingdoms.enroll(player, player.title(), LordRole.LORD, kingdom)


# ----------------------------------------------------------------------
# the reason is mandatory on every operation (D75)


async def test_every_operation_requires_a_reason() -> None:
    admin, kingdoms, store = await _launched()
    await _enroll_lord(kingdoms, "p1", "Aquitaine")
    with pytest.raises(AdminReasonRequiredError):
        await admin.rename_kingdom("Aquitaine", "Gascogne", reason="  ", **ACTOR)
    with pytest.raises(AdminReasonRequiredError):
        await admin.set_recruitment("Aquitaine", open=False, reason="", **ACTOR)
    assert store.admin_actions == {}


# ----------------------------------------------------------------------
# manual add, assignment, reassignment (D17)


async def test_add_lord_bypasses_the_queue_and_journals() -> None:
    admin, kingdoms, store = await _launched()
    lord = await admin.add_lord(
        "p1", "Player One", LordRole.LORD, "Aquitaine", reason="joined late", **ACTOR
    )
    assert lord.kingdom_id == "k-1"
    assert lord.in_queue is False
    [action] = await admin.journal.actions()
    assert action.action_type == "add_lord"
    assert action.reason == "joined late"
    assert store.admin_actions  # the journal entry is persisted
    with pytest.raises(AlreadyEnrolledError):
        await admin.add_lord("p1", "Again", LordRole.KING, "Aquitaine", reason="r", **ACTOR)


async def test_add_lord_respects_the_capacity_gel() -> None:
    admin, _, _ = await _launched()
    for index in range(4):  # the default quota is 4 lords per kingdom
        await admin.add_lord(
            f"p{index}", f"P{index}", LordRole.LORD, "Aquitaine", reason="r", **ACTOR
        )
    with pytest.raises(KingdomFullError):
        await admin.add_lord("p9", "P9", LordRole.LORD, "Aquitaine", reason="r", **ACTOR)


async def test_add_king_only_into_a_kingless_kingdom() -> None:
    admin, kingdoms, _ = await _free()
    await kingdoms.enroll("king1", "King One", LordRole.KING, proposed_name="Gascogne")
    with pytest.raises(ThroneSwapError):
        await admin.add_lord("p2", "P2", LordRole.KING, "Gascogne", reason="r", **ACTOR)


async def test_reassign_moves_a_lord_between_kingdoms() -> None:
    admin, kingdoms, _ = await _launched()
    await _enroll_lord(kingdoms, "p1", "Aquitaine")
    lord = await admin.reassign("p1", "Bourgogne", reason="D17 rebalance", **ACTOR)
    assert lord.kingdom_id == "k-2"
    assert lord.role is LordRole.LORD
    with pytest.raises(ReassignError):
        await admin.reassign("p1", "Bourgogne", reason="already there", **ACTOR)
    with pytest.raises(ReassignError):
        await admin.reassign("nobody", "Aquitaine", reason="not enrolled", **ACTOR)


async def test_eject_keeps_the_role_and_queues_the_player() -> None:
    admin, kingdoms, _ = await _launched()
    await _enroll_lord(kingdoms, "p1", "Aquitaine")
    lord = await admin.eject_to_queue("p1", reason="rule 13", **ACTOR)
    assert lord.in_queue is True
    assert lord.kingdom_id is None
    assert lord.role is LordRole.LORD


# ----------------------------------------------------------------------
# entities: create, rename, dissolve, throne swap


async def test_create_and_rollback_delete_the_kingdom_and_its_maps() -> None:
    admin, kingdoms, store = await _launched()
    await admin.set_quotas(kingdoms_count=3, reason="expansion", **ACTOR)
    kingdom = await admin.create_kingdom("Gascogne", reason="third kingdom", **ACTOR)
    assert kingdom.name == "Gascogne"
    drawn = [t for t in store.territories.values() if t["owner_kingdom_id"] == kingdom.id]
    assert drawn  # the creation drew the kingdom's maps
    action = next(
        entry for entry in await admin.journal.actions() if entry.action_type == "create_kingdom"
    )
    await admin.rollback(action.id, reason="wrong kingdom", **ACTOR)
    names = {k.name for k in await kingdoms.kingdoms()}
    assert "Gascogne" not in names
    assert all(t["owner_kingdom_id"] != kingdom.id for t in store.territories.values())
    # the rollback itself is journaled
    types = [entry.action_type for entry in await admin.journal.actions()]
    assert types[0] == "rollback"


async def test_rename_kingdom_validates_and_restores() -> None:
    admin, kingdoms, _ = await _launched()
    with pytest.raises(KingdomNameInvalidError):
        await admin.rename_kingdom("Aquitaine", "x!", reason="r", **ACTOR)
    with pytest.raises(KingdomNameInvalidError):
        await admin.rename_kingdom("Aquitaine", "Bourgogne", reason="duplicate", **ACTOR)
    await admin.rename_kingdom("Aquitaine", "Aquitania", reason="latin spelling", **ACTOR)
    assert any(k.name == "Aquitania" for k in await kingdoms.kingdoms())
    [action] = await admin.journal.actions()
    await admin.rollback(action.id, reason="contested", **ACTOR)
    assert any(k.name == "Aquitaine" for k in await kingdoms.kingdoms())


async def test_swap_throne_trades_roles_and_budgets() -> None:
    admin, kingdoms, store = await _free()
    king = await kingdoms.enroll("king1", "King One", LordRole.KING, proposed_name="Gascogne")
    await kingdoms.enroll("p1", "Player One", LordRole.LORD, "Gascogne")
    king.attack_used = 1
    await store.upsert_lord(king.to_mongo())
    new_king, old_king = await admin.swap_throne("Gascogne", "p1", reason="abdication", **ACTOR)
    assert old_king is not None
    assert new_king.role is LordRole.KING and new_king.attack_used == 1
    assert old_king.role is LordRole.LORD and old_king.attack_used == 0
    with pytest.raises(ThroneSwapError):
        await admin.swap_throne("Gascogne", "p1", reason="already the King", **ACTOR)


async def test_dissolve_sends_territories_to_gaia_and_players_to_queue() -> None:
    admin, kingdoms, store = await _free()
    king = await kingdoms.enroll("king1", "King One", LordRole.KING, proposed_name="Gascogne")
    assert king.kingdom_id is not None
    await _enroll_lord(kingdoms, "p1", "Gascogne")
    store.territories["t-1"] = TerritoryModel(
        _id="t-1",
        season_id=king.season_id,
        map_key="arabia",
        owner_kingdom_id=king.kingdom_id,
        drawn_at=datetime(2026, 10, 8, tzinfo=UTC),
    ).to_mongo()
    dissolved = await admin.dissolve_kingdom("Gascogne", reason="merger", **ACTOR)
    assert dissolved.name == "Gascogne"
    assert store.territories["t-1"]["owner_kingdom_id"] == GAIA_KINGDOM_KEY
    lords = {lord.id: lord for lord in await kingdoms.lords()}
    assert lords["king1"].in_queue and lords["king1"].role is LordRole.KING
    assert lords["p1"].in_queue and lords["p1"].role is LordRole.LORD
    assert all(k.name != "Gascogne" for k in await kingdoms.kingdoms())
    with pytest.raises(KingdomNotFoundError):
        await admin.dissolve_kingdom("Gascogne", reason="already gone", **ACTOR)


async def test_dissolve_rollback_restores_everything() -> None:
    admin, kingdoms, _ = await _free()
    king = await kingdoms.enroll("king1", "King One", LordRole.KING, proposed_name="Gascogne")
    lord = await kingdoms.enroll("p1", "Player One", LordRole.LORD, "Gascogne")
    await admin.dissolve_kingdom("Gascogne", reason="merger", **ACTOR)
    [action] = await admin.journal.actions()
    await admin.rollback(action.id, reason="called back", **ACTOR)
    assert any(k.name == "Gascogne" for k in await kingdoms.kingdoms())
    lords = {item.id: item for item in await kingdoms.lords()}
    assert lords["king1"].kingdom_id == king.kingdom_id
    assert lords["p1"].kingdom_id == lord.kingdom_id


# ----------------------------------------------------------------------
# switches and quotas (D75)


async def test_recruitment_switch_blocks_joining() -> None:
    admin, kingdoms, _ = await _launched()
    await admin.set_recruitment("Aquitaine", open=False, reason="full for now", **ACTOR)
    with pytest.raises(RecruitmentClosedError):
        await _enroll_lord(kingdoms, "p1", "Aquitaine")
    await admin.set_recruitment("Aquitaine", open=True, reason="reopened", **ACTOR)
    await _enroll_lord(kingdoms, "p1", "Aquitaine")


async def test_applications_switch_blocks_all_enrollment() -> None:
    admin, kingdoms, _ = await _launched()
    await admin.set_applications(open=False, reason="mid-season pause", **ACTOR)
    with pytest.raises(ApplicationsClosedError):
        await kingdoms.enroll("p1", "Player One", LordRole.LORD)
    with pytest.raises(ApplicationsClosedError):
        await _enroll_lord(kingdoms, "p2", "Aquitaine")


async def test_quota_overrides_apply_and_gel_never_eject() -> None:
    admin, kingdoms, _ = await _launched()
    await _enroll_lord(kingdoms, "p1", "Aquitaine")
    await _enroll_lord(kingdoms, "p2", "Aquitaine")
    # lower the quota below the headcount: gel, no ejection
    await admin.set_quotas(lords_per_kingdom=1, reason="denser kingdoms", **ACTOR)
    lords = [lord for lord in await kingdoms.lords() if lord.kingdom_id == "k-1"]
    assert len(lords) == 2  # both members stay
    with pytest.raises(KingdomFullError):
        await _enroll_lord(kingdoms, "p3", "Aquitaine")
    # raise both quotas: new members fit and a third kingdom fits
    await admin.set_quotas(kingdoms_count=3, lords_per_kingdom=4, reason="expansion", **ACTOR)
    await _enroll_lord(kingdoms, "p3", "Aquitaine")
    await admin.create_kingdom("Gascogne", reason="third kingdom", **ACTOR)
    # None resets both to the config defaults (2 kingdoms / 4 lords)
    await admin.set_quotas(reason="back to defaults", **ACTOR)
    with pytest.raises(KingdomLimitError):
        await admin.create_kingdom("Normandie", reason="one too many", **ACTOR)


async def test_foundation_rights_window_and_gates() -> None:
    admin, kingdoms, store = await _free()
    await admin.set_foundation_rights(king=False, reason="admin-founded season", **ACTOR)
    with pytest.raises(FoundationClosedError):
        await kingdoms.enroll("king1", "King One", LordRole.KING, proposed_name="Gascogne")
    # the window closes with the first completed cycle
    [season] = store.seasons.values()
    season_doc = dict(season)
    season_doc["current_cycle"] = 1
    await store.upsert_season(season_doc)
    with pytest.raises(FoundationWindowClosedError):
        await admin.set_foundation_rights(king=True, reason="too late", **ACTOR)


# ----------------------------------------------------------------------
# rollback mechanics on roster actions


async def test_eject_rollback_restores_the_lord() -> None:
    admin, kingdoms, _ = await _launched()
    await _enroll_lord(kingdoms, "p1", "Aquitaine")
    await admin.eject_to_queue("p1", reason="rule 13", **ACTOR)
    [action] = await admin.journal.actions()
    await admin.rollback(action.id, reason="wrong player", **ACTOR)
    lord = next(item for item in await kingdoms.lords() if item.id == "p1")
    assert lord.kingdom_id == "k-1"
    assert lord.in_queue is False


async def test_king_cannot_be_ejected_or_reassigned() -> None:
    admin, kingdoms, _ = await _free()
    await kingdoms.enroll("king1", "King One", LordRole.KING, proposed_name="Gascogne")
    with pytest.raises(EjectKingError):
        await admin.eject_to_queue("king1", reason="r", **ACTOR)
    with pytest.raises(ReassignError):
        await admin.reassign("king1", "Gascogne", reason="r", **ACTOR)
