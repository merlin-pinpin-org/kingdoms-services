"""Kingdoms mod season closing & ShowMatch - unit tests (kingdoms-services#162, T8).

Reference §23 behind an in-memory store: the Conquest finish on the
territory counts (D16), the ShowMatch PA2 deciding a tie - duels 1v1
first, escalating 2v2/3v3 once every lord has dueled, maps from the
finalists' own territories and civilizations never repeating, with the
Megarandom fallback (D50) - and the archive snapshot before the next
season's wholesale reset (D38/D52).
"""
from __future__ import annotations

import pytest

from kingdoms.mods.kingdoms.config import (
    CivilizationCondition,
    KingdomsSeasonConfig,
    ShowMatchSettings,
    default_map_catalog,
)
from kingdoms.mods.kingdoms.models import GAIA_KINGDOM_KEY, LordRole
from kingdoms.mods.kingdoms.season_end import SeasonEndService, ShowMatchError
from kingdoms.mods.kingdoms.service import KingdomsService
from kingdoms.mods.kingdoms.territories import TerritoryService

from .test_kingdoms_service import MemoryStore as _BaseStore


class MemoryStore(_BaseStore):
    """The enrollment store extended with every T3-T8 collection."""

    def __init__(self) -> None:
        super().__init__()
        self.territories: dict[str, dict] = {}
        self.attacks: dict[str, dict] = {}
        self.technologies: dict[str, dict] = {}
        self.showmatches: dict[str, dict] = {}

    async def upsert_territory(self, document: dict) -> None:
        self.territories[document["_id"]] = document

    async def find_territories(self) -> list[dict]:
        return list(self.territories.values())

    async def delete_territory(self, territory_id: str) -> None:
        self.territories.pop(territory_id, None)

    async def upsert_attack(self, document: dict) -> None:
        self.attacks[document["_id"]] = document

    async def find_attacks(self) -> list[dict]:
        return list(self.attacks.values())

    async def delete_attack(self, attack_id: str) -> None:
        self.attacks.pop(attack_id, None)

    async def upsert_technology(self, document: dict) -> None:
        self.technologies[document["_id"]] = document

    async def find_technologies(self) -> list[dict]:
        return list(self.technologies.values())

    async def upsert_showmatch(self, document: dict) -> None:
        self.showmatches[document["_id"]] = document

    async def find_showmatches(self) -> list[dict]:
        return list(self.showmatches.values())


def _config() -> KingdomsSeasonConfig:
    """A config with a civilization catalog for the ShowMatch draws."""
    return KingdomsSeasonConfig(
        maps=default_map_catalog(),
        civilizations=tuple(
            CivilizationCondition(key=key, display_name=key.capitalize())
            for key in (
                "britons", "francs", "celtes", "goths", "hongrois", "italiens",
                "franks2", "teutons", "vikings", "byzantins",
            )
        ),
    )


class Bundle:
    """Everything one season-end test needs, rebuilt per test."""

    def __init__(self, config=None) -> None:
        self.store = MemoryStore()
        self.config = config or _config()
        self.kingdoms = KingdomsService(self.store, self.config)  # type: ignore[arg-type]
        self.territories = TerritoryService(self.store, self.config, self.kingdoms)  # type: ignore[arg-type]
        self.season_end = SeasonEndService(  # type: ignore[arg-type]
            self.store, self.config, self.kingdoms, self.territories
        )

    async def launch_season(self) -> tuple[str, str, str]:
        """Launch a free-mode season with two kingdoms and four lords."""
        await self.kingdoms.launch()
        await self.kingdoms.enroll("king-a", "King A", LordRole.KING, proposed_name="Aquitaine")
        await self.kingdoms.enroll("lord-a", "Lord A", LordRole.LORD, kingdom_name="Aquitaine")
        await self.kingdoms.enroll("king-b", "King B", LordRole.KING, proposed_name="Bourgogne")
        await self.kingdoms.enroll("lord-b", "Lord B", LordRole.LORD, kingdom_name="Bourgogne")
        return "Aquitaine", "Bourgogne", "gaia"

    async def kingdom_id(self, name: str) -> str:
        """Resolve a kingdom id by display name."""
        return next(k.id for k in await self.kingdoms.kingdoms() if k.name == name)

    async def draw(self, seed: int) -> None:
        """Draw the initial territories deterministically."""
        await self.territories.draw_initial(seed=seed)

    async def tie_the_counts(self) -> None:
        """Equalize the territory counts of the two player kingdoms."""
        await self.draw(1)
        aquitaine, bourgogne = await self.kingdom_id("Aquitaine"), await self.kingdom_id("Bourgogne")
        counts = await self.territories.territory_count_by_kingdom()
        if counts[aquitaine] == counts[bourgogne]:
            return
        rich, poor = (
            (aquitaine, bourgogne)
            if counts[aquitaine] > counts[bourgogne]
            else (bourgogne, aquitaine)
        )
        territories = await self.territories.territories()
        for territory in territories:
            if counts[rich] == counts[poor]:
                break
            if territory.owner_kingdom_id == rich:
                await self.territories.transfer(territory.id, poor)
                counts[rich] -= 1
                counts[poor] += 1


async def test_conquest_finish_crowns_the_leader() -> None:
    """D16: the territory counts decide; the season closes idempotently."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.draw(1)
    aquitaine = await bundle.kingdom_id("Aquitaine")
    bourgogne = await bundle.kingdom_id("Bourgogne")
    captured = next(
        t for t in await bundle.territories.territories() if t.owner_kingdom_id == bourgogne
    )
    await bundle.territories.transfer(captured.id, aquitaine)
    report = await bundle.season_end.close_season()
    assert report["winner_kingdom_id"] == aquitaine
    season = await bundle.kingdoms.current_season()
    assert season is not None
    assert season.finished is True
    assert season.winner_kingdom_id == report["winner_kingdom_id"]
    # Idempotent: closing again returns the same verdict.
    again = await bundle.season_end.close_season()
    assert again["winner_kingdom_id"] == report["winner_kingdom_id"]


async def test_tie_opens_a_showmatch() -> None:
    """D50: a tie at the top leaves the winner to the ShowMatch PA2."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.tie_the_counts()
    report = await bundle.season_end.close_season()
    assert report["winner_kingdom_id"] is None
    assert report["showmatch"] is True
    match = await bundle.season_end.require_showmatch()
    aquitaine, bourgogne = await bundle.kingdom_id("Aquitaine"), await bundle.kingdom_id("Bourgogne")
    assert {match.kingdom_a, match.kingdom_b} == {aquitaine, bourgogne}


async def test_showmatch_2_0_closes_the_season() -> None:
    """D50: two wins for one side end the ShowMatch and crown it."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.tie_the_counts()
    await bundle.season_end.close_season()
    first = await bundle.season_end.next_duel(seed=1)
    assert first.format == "1v1"
    assert len(first.side_a) == 1 and len(first.side_b) == 1
    match = await bundle.season_end.record_duel_result(first.index, "a")
    assert match.score_a == 1 and match.finished is False
    second = await bundle.season_end.next_duel(seed=2)
    match = await bundle.season_end.record_duel_result(second.index, "a")
    assert match.finished is True
    assert match.score_a == 2 and match.score_b == 0
    season = await bundle.kingdoms.current_season()
    assert season is not None
    assert season.winner_kingdom_id == match.kingdom_a


async def test_showmatch_1_1_chains_then_resolves() -> None:
    """D50: a 1-1 tie chains new duels with other lords."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.tie_the_counts()
    await bundle.season_end.close_season()
    first = await bundle.season_end.next_duel(seed=1)
    await bundle.season_end.record_duel_result(first.index, "a")
    second = await bundle.season_end.next_duel(seed=2)
    await bundle.season_end.record_duel_result(second.index, "b")
    match = await bundle.season_end.require_showmatch()
    assert match.score_a == 1 and match.score_b == 1
    assert match.finished is False
    third = await bundle.season_end.next_duel(seed=3)
    match = await bundle.season_end.record_duel_result(third.index, "a")
    assert match.finished is True
    assert match.score_a == 2


async def test_showmatch_maps_and_civs_never_repeat() -> None:
    """D50: maps come from the finalists' territories, civs never repeat."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.tie_the_counts()
    await bundle.season_end.close_season()
    aquitaine, bourgogne = await bundle.kingdom_id("Aquitaine"), await bundle.kingdom_id("Bourgogne")
    owned = {
        t.map_key
        for t in await bundle.territories.territories()
        if t.owner_kingdom_id in (aquitaine, bourgogne)
    }
    seen_maps: set[str] = set()
    seen_civs: set[str] = set()
    for seed in range(1, 5):
        duel = await bundle.season_end.next_duel(seed=seed)
        assert duel.map_key in owned or duel.map_key == bundle.config.showmatch.fallback_map_key
        assert duel.map_key not in seen_maps
        seen_maps.add(duel.map_key)
        for civ in duel.civilizations.values():
            assert civ not in seen_civs
            seen_civs.add(civ)


async def test_showmatch_escalates_when_every_lord_has_dueled() -> None:
    """D50: 1v1 first, then 2v2, then 3v3 once the rosters are spent."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.tie_the_counts()
    await bundle.season_end.close_season()
    formats: list[str] = []
    for seed in range(1, 8):
        duel = await bundle.season_end.next_duel(seed=seed)
        formats.append(duel.format)
        # Alternate the winners: the ShowMatch keeps chaining (1-1).
        await bundle.season_end.record_duel_result(duel.index, "a" if seed % 2 else "b")
        match = await bundle.season_end.require_showmatch()
        if match.finished:
            break
    # Two kingdoms of two lords: the 1v1 pairs run out, the ladder escalates.
    assert formats[0] == "1v1"
    assert "2v2" in formats[:4]


async def test_showmatch_falls_back_to_megarandom() -> None:
    """D50: exhausted own-territory maps fall back to the configured map."""
    config = _config().model_copy(
        update={"showmatch": ShowMatchSettings(wins_needed=10)}
    )
    bundle = Bundle(config=config)
    await bundle.launch_season()
    await bundle.tie_the_counts()
    await bundle.season_end.close_season()
    fallback = bundle.config.showmatch.fallback_map_key
    maps: list[str] = []
    for seed in range(1, 16):
        duel = await bundle.season_end.next_duel(seed=seed)
        maps.append(duel.map_key)
        await bundle.season_end.record_duel_result(duel.index, "a" if seed % 2 else "b")
        match = await bundle.season_end.require_showmatch()
        if match.finished:
            break
    assert fallback in maps
    # The own-territory maps never repeat (only the fallback may).
    own = [m for m in maps if m != fallback]
    assert len(own) == len(set(own))


async def test_record_result_refuses_bad_inputs() -> None:
    """D50: an unknown side, a missing duel or a replay all raise."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.tie_the_counts()
    await bundle.season_end.close_season()
    duel = await bundle.season_end.next_duel(seed=1)
    with pytest.raises(ShowMatchError):
        await bundle.season_end.record_duel_result(duel.index, "c")
    with pytest.raises(ShowMatchError):
        await bundle.season_end.record_duel_result(99, "a")
    await bundle.season_end.record_duel_result(duel.index, "b")
    with pytest.raises(ShowMatchError):
        await bundle.season_end.record_duel_result(duel.index, "b")


async def test_archive_snapshots_every_collection() -> None:
    """D38/D52: the archive holds every season-scoped collection."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.draw(1)
    archive = await bundle.season_end.archive()
    assert archive["seasons"]
    assert archive["kingdoms"]
    assert archive["lords"]
    assert archive["territories"]
    gaia = next(k for k in await bundle.kingdoms.kingdoms() if k.name == GAIA_KINGDOM_KEY)
    assert any(doc["_id"] == gaia.id for doc in archive["kingdoms"])
