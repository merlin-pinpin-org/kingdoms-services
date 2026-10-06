"""Kingdoms mod territories & maps — unit tests (kingdoms-services#163, T3).

Reference §6-§10 behind an in-memory store: the admin-managed map
catalog as data, the random initial draw without duplicates, the
"map is out" season state, the admin ejection with an automatic
replacement, and the idempotent ownership transfer reused by T4/T5/T7.
"""
from __future__ import annotations

import pytest

from kingdoms.mods.kingdoms.config import (
    KingdomsSeasonConfig,
    MapEntry,
    default_map_catalog,
    default_season_config,
)
from kingdoms.mods.kingdoms.models import GAIA_KINGDOM_KEY
from kingdoms.mods.kingdoms.service import KingdomNotFoundError, KingdomsService, NoSeasonError
from kingdoms.mods.kingdoms.territories import (
    MapPoolExhaustedError,
    TerritoryNotFoundError,
    TerritoryService,
)


class MemoryStore:
    """In-memory KingdomsStore — the domain tests stay network-free."""

    def __init__(self) -> None:
        self.seasons: dict[str, dict] = {}
        self.kingdoms: dict[str, dict] = {}
        self.lords: dict[str, dict] = {}
        self.territories: dict[str, dict] = {}

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

    async def delete_territory(self, territory_id: str) -> None:
        self.territories.pop(territory_id, None)

    async def wipe_season_data(self) -> None:
        self.seasons.clear()
        self.kingdoms.clear()
        self.lords.clear()
        self.territories.clear()


def _services(
    config: KingdomsSeasonConfig | None = None,
) -> tuple[TerritoryService, KingdomsService, MemoryStore]:
    store = MemoryStore()
    kingdoms = KingdomsService(store, config or default_season_config())  # type: ignore[arg-type]
    return TerritoryService(store, kingdoms.config, kingdoms), kingdoms, store  # type: ignore[arg-type]


def _tiny_config(maps: list[str]) -> KingdomsSeasonConfig:
    """A minimal config: 2 kingdoms, 1 territory each, 1 Gaïa map."""
    return KingdomsSeasonConfig(
        territories_per_kingdom=1,
        gaia_territories=1,
        maps=tuple(MapEntry(key=key, display_name=key) for key in maps),
    )


async def test_draw_requires_a_running_season() -> None:
    service, _, _ = _services()
    with pytest.raises(NoSeasonError):
        await service.draw_initial()


async def test_initial_draw_distributes_n_plus_m_unique_maps() -> None:
    """2 player kingdoms x 5 + 8 Gaia = 18 distinct maps (§6.3)."""
    service, kingdoms, _ = _services()
    await kingdoms.launch(["Aquitaine", "Bourgogne"])
    created = await service.draw_initial(seed=42)
    assert len(created) == 18
    keys = [territory.map_key for territory in created]
    assert len(set(keys)) == 18
    all_created = await service.territories()
    owners = {territory.owner_kingdom_id for territory in all_created}
    assert len(owners) == 3  # two player kingdoms + Gaïa


async def test_initial_draw_fails_atomically_when_catalog_too_small() -> None:
    """§7: a too-small list fails cleanly, nothing is persisted."""
    config = _tiny_config(["arabia", "arena"])
    service, kingdoms, store = _services(config)
    await kingdoms.launch(["Aquitaine", "Bourgogne"])
    with pytest.raises(MapPoolExhaustedError) as excinfo:
        await service.draw_initial()
    assert excinfo.value.missing == 1  # 3 wanted, 2 in catalog
    assert not store.territories


async def test_draw_is_deterministic_with_a_seed() -> None:
    service, kingdoms, _ = _services()
    await kingdoms.launch(["Aquitaine", "Bourgogne"])
    first = await service.draw_initial(seed=7)
    keys_first = [territory.map_key for territory in first]
    service2, kingdoms2, _ = _services()
    await kingdoms2.launch(["Aquitaine", "Bourgogne"])
    second = await service2.draw_initial(seed=7)
    assert keys_first == [territory.map_key for territory in second]


async def test_drawn_map_is_out_for_the_season() -> None:
    """§8: a drawn map never comes back during the season."""
    service, kingdoms, _ = _services()
    await kingdoms.launch(["Aquitaine"])
    await service.draw_initial(seed=1)
    drawn = await service.drawn_map_keys()
    assert drawn
    catalog_keys = {entry.key for entry in default_map_catalog()}
    assert len(catalog_keys - drawn) > 0


async def test_transfer_is_idempotent_and_validated() -> None:
    service, kingdoms, _ = _services()
    await kingdoms.launch(["Aquitaine", "Bourgogne"])
    created = await service.draw_initial(seed=3)
    territory = created[0]
    new_owner = next(k.id for k in await kingdoms.kingdoms() if k.id != territory.owner_kingdom_id)
    moved = await service.transfer(territory.id, new_owner)
    assert moved.owner_kingdom_id == new_owner
    # Idempotent: transferring again to the same owner is a no-op.
    again = await service.transfer(territory.id, new_owner)
    assert again.owner_kingdom_id == new_owner
    with pytest.raises(KingdomNotFoundError):
        await service.transfer(territory.id, "k-nope")


async def test_eject_replaces_and_keeps_the_map_out() -> None:
    """§9: ejection replaces for the same owner; the ejected map stays out."""
    service, kingdoms, _ = _services()
    await kingdoms.launch(["Aquitaine"])
    created = await service.draw_initial(seed=5)
    territory = created[0]
    replacement = await service.eject(territory.map_key, seed=11)
    assert replacement.id == territory.id
    assert replacement.owner_kingdom_id == territory.owner_kingdom_id
    assert replacement.map_key != territory.map_key
    # The ejected map is recorded out on the season document.
    season = await kingdoms.current_season()
    assert territory.map_key in (season.out_maps if season else [])
    # And a second ejection never re-draws the ejected map.
    second = await service.eject(replacement.map_key, seed=12)
    assert second.map_key not in {territory.map_key, replacement.map_key}


async def test_eject_unknown_map_raises() -> None:
    service, kingdoms, _ = _services()
    await kingdoms.launch(["Aquitaine"])
    await service.draw_initial(seed=1)
    with pytest.raises(TerritoryNotFoundError):
        await service.eject("never-drawn-map")


async def test_eject_without_replacement_fails_atomically() -> None:
    """No non-out map remains: the admin is warned, nothing changes."""
    config = _tiny_config(["arabia", "arena", "oasis"])
    service, kingdoms, store = _services(config)
    await kingdoms.launch(["Aquitaine", "Bourgogne"])
    created = await service.draw_initial(seed=0)
    before = dict(store.territories)
    with pytest.raises(MapPoolExhaustedError):
        await service.eject(created[0].map_key)
    assert store.territories == before


async def test_catalog_metadata_and_cadastre_are_data() -> None:
    """Cadastre effects are admin-editable data, not code (#163 spec 3)."""
    config = KingdomsSeasonConfig(
        maps=(
            MapEntry(
                key="black-forest",
                display_name="Forêt Noire",
                cadastre={"civ_bonus": "celts", "tech_points": 1},
            ),
        ),
        territories_per_kingdom=1,
        gaia_territories=0,
    )
    assert config.maps[0].cadastre == {"civ_bonus": "celts", "tech_points": 1}
    assert config.maps[0].display_name == "Forêt Noire"


async def test_season_launch_resets_out_maps() -> None:
    """A new season resets the out-map state wholesale (§8, D38)."""
    service, kingdoms, _ = _services()
    await kingdoms.launch(["Aquitaine"])
    created = await service.draw_initial(seed=2)
    await service.eject(created[0].map_key, seed=13)
    assert await kingdoms.current_season() is not None
    await kingdoms.launch(["Aquitaine"])
    season = await kingdoms.current_season()
    assert season is not None and season.out_maps == []


async def test_gaia_initial_draw_targets_gaia_kingdom() -> None:
    service, kingdoms, _ = _services()
    await kingdoms.launch(["Aquitaine"])
    await service.draw_initial(seed=4)
    kingdoms_list = await kingdoms.kingdoms()
    gaia = next(kingdom for kingdom in kingdoms_list if kingdom.name == GAIA_KINGDOM_KEY)
    counts = await service.territory_count_by_kingdom()
    assert counts.get(gaia.id) == default_season_config().gaia_territories
