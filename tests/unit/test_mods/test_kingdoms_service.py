"""Kingdoms mod season & enrollment service — unit tests (kingdoms-services#157).

The domain rules of reference §3-§5 behind an in-memory store: manual
launch (free or imposed), wholesale reset, King/Lord enrollment, the
waiting queue, departures and the D23 replacement that inherits the
weekly budgets — plus the D21 name rules.
"""
from __future__ import annotations

import pytest

from kingdoms.mods.kingdoms.config import default_season_config
from kingdoms.mods.kingdoms.models import GAIA_KINGDOM_KEY, LordRole
from kingdoms.mods.kingdoms.service import (
    AlreadyEnrolledError,
    ImposedKingdomsError,
    KingdomFullError,
    KingdomLimitError,
    KingdomNameInvalidError,
    KingdomNotFoundError,
    KingdomsService,
    NoSeasonError,
    NotEnrollableError,
    NotQueuedError,
    ReplacementError,
)


class MemoryStore:
    """In-memory KingdomsStore — the domain tests stay network-free."""

    def __init__(self) -> None:
        self.seasons: dict[str, dict] = {}
        self.kingdoms: dict[str, dict] = {}
        self.lords: dict[str, dict] = {}
        self.territories: dict[str, dict] = {}
        self.admin_actions: dict[str, dict] = {}

    async def upsert_season(self, document: dict) -> None:
        self.seasons[document["_id"]] = document

    async def find_seasons(self) -> list[dict]:
        return list(self.seasons.values())

    async def upsert_kingdom(self, document: dict) -> None:
        self.kingdoms[document["_id"]] = document

    async def find_kingdoms(self) -> list[dict]:
        return list(self.kingdoms.values())

    async def delete_kingdom(self, kingdom_id: str) -> None:
        self.kingdoms.pop(kingdom_id, None)

    async def upsert_lord(self, document: dict) -> None:
        self.lords[document["_id"]] = document

    async def find_lords(self) -> list[dict]:
        return list(self.lords.values())

    async def delete_lord(self, lord_id: str) -> None:
        self.lords.pop(lord_id, None)

    async def upsert_territory(self, document: dict) -> None:
        self.territories[document["_id"]] = document

    async def find_territories(self) -> list[dict]:
        return list(self.territories.values())

    async def wipe_season_data(self) -> None:
        self.seasons.clear()
        self.kingdoms.clear()
        self.lords.clear()
        self.territories.clear()
        self.admin_actions.clear()

    async def insert_admin_action(self, document: dict) -> None:
        self.admin_actions[document["_id"]] = document

    async def find_admin_actions(self) -> list[dict]:
        return list(self.admin_actions.values())

    async def upsert_admin_action(self, document: dict) -> None:
        self.admin_actions[document["_id"]] = document


def _service() -> tuple[KingdomsService, MemoryStore]:
    store = MemoryStore()
    return KingdomsService(store, default_season_config()), store  # type: ignore[arg-type]


async def test_enroll_requires_a_running_season() -> None:
    service, _ = _service()
    with pytest.raises(NoSeasonError):
        await service.enroll("p1", "Player One", LordRole.LORD)


async def test_launch_creates_season_and_gaia() -> None:
    service, store = _service()
    season = await service.launch()
    assert season.imposed_kingdoms is False
    kingdoms = await service.kingdoms()
    assert [kingdom.name for kingdom in kingdoms] == [GAIA_KINGDOM_KEY]
    assert kingdoms[0].name_approved is True
    assert store.seasons  # the season document is persisted


async def test_launch_imposed_kingdoms_seeds_them() -> None:
    service, _ = _service()
    season = await service.launch(["Aquitaine", "Bourgogne"])
    assert season.imposed_kingdoms is True
    names = {kingdom.name for kingdom in await service.kingdoms()}
    assert {"Aquitaine", "Bourgogne", GAIA_KINGDOM_KEY} <= names


async def test_reset_wipes_the_season_data() -> None:
    service, _ = _service()
    await service.launch(["Aquitaine"])
    await service.reset()
    assert await service.current_season() is None
    assert await service.kingdoms() == []


async def test_king_founds_a_pending_name_kingdom() -> None:
    service, _ = _service()
    await service.launch()
    lord = await service.enroll("p1", "Arthur", LordRole.KING, proposed_name="Avalon")
    assert lord.role is LordRole.KING
    kingdom = next(k for k in await service.kingdoms() if not k.is_gaia)
    assert kingdom.name == "Avalon"
    assert kingdom.name_approved is False


async def test_king_forbidden_in_the_imposed_mode() -> None:
    service, _ = _service()
    await service.launch(["Aquitaine"])
    with pytest.raises(ImposedKingdomsError):
        await service.enroll("p1", "Arthur", LordRole.KING, proposed_name="Avalon")


async def test_kingdom_name_rules_are_enforced() -> None:
    service, _ = _service()
    await service.launch()
    with pytest.raises(KingdomNameInvalidError):
        await service.enroll("p1", "Arthur", LordRole.KING, proposed_name="x")
    with pytest.raises(KingdomNameInvalidError):
        await service.enroll("p2", "Lancelot", LordRole.KING, proposed_name="bad;name!")


async def test_kingdom_count_limit_is_enforced() -> None:
    service, _ = _service()
    await service.launch()
    await service.enroll("p1", "Arthur", LordRole.KING, proposed_name="Avalon")
    await service.enroll("p2", "Lancelot", LordRole.KING, proposed_name="Camelot")
    with pytest.raises(KingdomLimitError):
        await service.enroll("p3", "Gauvain", LordRole.KING, proposed_name="Tintagel")


async def test_lord_joins_a_kingdom_and_double_enrollment_fails() -> None:
    service, _ = _service()
    await service.launch(["Aquitaine"])
    lord = await service.enroll("p1", "Rollon", LordRole.LORD, kingdom_name="Aquitaine")
    assert lord.kingdom_id is not None
    with pytest.raises(AlreadyEnrolledError):
        await service.enroll("p1", "Rollon", LordRole.LORD, kingdom_name="Aquitaine")


async def test_gaia_is_never_enrollable() -> None:
    service, _ = _service()
    await service.launch(["Aquitaine"])
    with pytest.raises(NotEnrollableError):
        await service.enroll("p1", "Rollon", LordRole.LORD, kingdom_name=GAIA_KINGDOM_KEY)


async def test_lord_capacity_is_enforced() -> None:
    service, _ = _service()
    await service.launch(["Aquitaine"])
    for index in range(service.config.lords_per_kingdom):
        await service.enroll(f"p{index}", f"Lord {index}", LordRole.LORD, kingdom_name="Aquitaine")
    with pytest.raises(KingdomFullError):
        await service.enroll("p99", "Trop Tard", LordRole.LORD, kingdom_name="Aquitaine")


async def test_lord_without_kingdom_waits_in_the_queue() -> None:
    service, _ = _service()
    await service.launch(["Aquitaine"])
    lord = await service.enroll("p1", "Rollon", LordRole.LORD)
    assert lord.in_queue is True
    assert lord.kingdom_id is None


async def test_assign_moves_a_queued_player() -> None:
    service, _ = _service()
    await service.launch(["Aquitaine"])
    await service.enroll("p1", "Rollon", LordRole.LORD)
    lord = await service.assign("p1", "Aquitaine", LordRole.LORD)
    assert lord.in_queue is False
    assert lord.kingdom_id is not None
    with pytest.raises(NotQueuedError):
        await service.assign("p1", "Aquitaine", LordRole.LORD)


async def test_assign_unknown_kingdom_fails() -> None:
    service, _ = _service()
    await service.launch(["Aquitaine"])
    await service.enroll("p1", "Rollon", LordRole.LORD)
    with pytest.raises(KingdomNotFoundError):
        await service.assign("p1", "Narnia", LordRole.LORD)


async def test_leave_records_the_reason() -> None:
    service, _ = _service()
    await service.launch(["Aquitaine"])
    await service.enroll("p1", "Rollon", LordRole.LORD, kingdom_name="Aquitaine")
    await service.leave("p1", "RL")
    lord = next(item for item in await service.lords() if item.id == "p1")
    assert lord.left is True
    assert lord.left_reason == "RL"


async def test_replace_inherits_the_weekly_budgets() -> None:
    service, store = _service()
    await service.launch(["Aquitaine"])
    await service.enroll("p1", "Rollon", LordRole.LORD, kingdom_name="Aquitaine")
    outgoing = next(item for item in await service.lords() if item.id == "p1")
    outgoing.attack_used = 1
    outgoing.defense_used = 1
    await store.upsert_lord(outgoing.to_mongo())  # persist the spent budgets
    await service.leave("p1", "RL")
    await service.enroll("p2", "Le Successeur", LordRole.LORD)  # queued
    incoming = await service.replace("p1", "p2")
    assert incoming.attack_used == 1
    assert incoming.defense_used == 1
    assert incoming.kingdom_id == outgoing.kingdom_id
    assert all(item.id != "p1" for item in await service.lords())  # the outgoing doc is gone


async def test_replace_requires_the_outgoing_to_have_left() -> None:
    service, _ = _service()
    await service.launch(["Aquitaine"])
    await service.enroll("p1", "Rollon", LordRole.LORD, kingdom_name="Aquitaine")
    await service.enroll("p2", "Le Successeur", LordRole.LORD)
    with pytest.raises(ReplacementError):
        await service.replace("p1", "p2")


async def test_decide_name_approves_or_falls_back() -> None:
    service, _ = _service()
    await service.launch()
    await service.enroll("p1", "Arthur", LordRole.KING, proposed_name="Avalon")
    approved = await service.decide_name("Avalon", approved=True)
    assert approved.name == "Avalon"
    assert approved.name_approved is True
    refused = await service.decide_name("Avalon", approved=False)
    assert refused.name != "Avalon"
    assert refused.name_approved is True


async def test_re_enroll_after_leave_is_allowed() -> None:
    service, _ = _service()
    await service.launch(["Aquitaine"])
    await service.enroll("p1", "Rollon", LordRole.LORD, kingdom_name="Aquitaine")
    await service.leave("p1", "RL")
    lord = await service.enroll("p1", "Rollon", LordRole.LORD)
    assert lord.in_queue is True
