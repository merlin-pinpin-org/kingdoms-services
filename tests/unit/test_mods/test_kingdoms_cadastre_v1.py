"""Kingdoms cadastre-diplomatie-v1 — unit tests (Drasah 15/10/2026).

The official 97… — the official Drasah map list ERASES the v0
catalog (FR labels verbatim, cadastre bonus verbatim, the 12 Drasah
tags — GARNISON ignored in V1), and the Diplomatie engine grows from
the binary v0 to the threshold counters: N maps of a tag, total map
counts, tech points, marriages and held civilizations. The 55
declared civilizations of ``season.yaml`` all carry a condition.
"""
from __future__ import annotations

from pathlib import Path

from kingdoms.mods.kingdoms.config import (
    CivilizationCondition,
    Epoch,
    KingdomsSeasonConfig,
    default_map_catalog,
    load_season_config,
)

from .test_kingdoms_diplomacy import Bundle

REPO_ROOT = Path(__file__).resolve().parents[3]


# ----------------------------------------------------------------------
# The Cadastre — Drasah's official list erases the v0 catalog
# ----------------------------------------------------------------------
def test_catalog_matches_drasahs_official_list() -> None:
    """96 verbatim maps (Drasah's table), unique keys, FR labels."""
    catalog = default_map_catalog()
    assert len(catalog) == 96
    keys = [entry.key for entry in catalog]
    assert len(set(keys)) == len(keys)
    by_key = {entry.key: entry for entry in catalog}
    # the v0-only maps DO NOT EXIST anymore.
    for gone in ("altai", "babel", "hideout", "highland", "siberia"):
        assert gone not in by_key
    # the survivors keep their key, adopt Drasah's French label.
    assert by_key["arabia"].display_name == "Arabie"
    assert by_key["black-forest"].display_name == "Forêt noire"
    assert by_key["african-clearing"].display_name == "Clairière Africaine"
    assert by_key["gold-rush"].display_name == "Ruée vers l'or"
    assert by_key["migration"].display_name == "Emigration"
    # verbatim bonus spot checks.
    assert by_key["baltique"].cadastre["bonus"] == (
        "Réduit d'une heure le délai de l'une de vos agressions classiques"
    )
    assert by_key["passage-montagneux"].cadastre["bonus"] == (
        "Augmente le délai d'attaque minimum de vos adversaires de 1h"
    )
    assert by_key["le-fort-de-la-colline"].cadastre["bonus"] == (
        "Augmente votre capacité de patrouille de 1"
    )


def test_catalog_carries_drasahs_tags_not_the_v0_flags() -> None:
    """The 12 Drasah tags replace the v0 flags — NOT 1:1."""
    by_key = {entry.key: entry for entry in default_map_catalog()}
    # the spec's explicit corrections.
    assert by_key["falaise"].types.chaotique and not by_key["falaise"].types.montagne
    assert by_key["cote"].types.hybride and not by_key["cote"].types.eau
    assert by_key["douves-d-equipe"].types.hybride and not by_key["douves-d-equipe"].types.mur_depart
    assert by_key["faille-sableuse"].types.desert and by_key["faille-sableuse"].types.ouverte
    assert by_key["ghost-lake"].types.ouverte and not by_key["ghost-lake"].types.eau
    assert by_key["yucatan"].types.ferme
    assert by_key["archipel"].types.hybride and by_key["archipel"].types.eau
    assert by_key["forteresse-circulaire"].types.mur_depart and not by_key["forteresse-circulaire"].types.objectif
    # unknown tags read as False, never raise.
    assert not by_key["yucatan"].types.has("lakes")


def test_garnison_is_ignored_in_v1() -> None:
    """GARNISON is priority 3: garnison-only maps carry NO bonus."""
    by_key = {entry.key: entry for entry in default_map_catalog()}
    for key in ("clairiere-fortifiee", "douves-d-equipe", "isthme"):
        assert not by_key[key].cadastre, f"{key} must carry no bonus in V1"
    # the maps keeping only their FIRST bonus.
    assert by_key["embranchement"].cadastre["bonus"] == "Offre un sabotage/semaine"
    assert by_key["le-passage"].cadastre["bonus"] == "Offre une exploration/dimanche"


# ----------------------------------------------------------------------
# The Diplomatie — season.yaml data and the threshold engine
# ----------------------------------------------------------------------
def test_season_yaml_declares_every_civilization_conditioned() -> None:
    """All 55 declared civs carry at least one condition, refs valid."""
    config = load_season_config(REPO_ROOT / "config")
    assert len(config.civilizations) == 55
    catalog = {entry.key for entry in config.maps}
    for civ in config.civilizations:
        assert not (
            civ.requires_civilization is None
            and not civ.requires_map_keys
            and not civ.requires_map_types
            and civ.min_map_count is None
            and civ.max_map_count is None
            and civ.requires_kingdom_name_pattern is None
            and civ.requires_tech_points is None
            and civ.requires_marriages is None
            and civ.requires_civilization_count is None
        ), f"{civ.key} has no condition"
        for key in civ.requires_map_keys:
            assert key in catalog, f"{civ.key} references unknown map {key}"
    by_key = {civ.key: civ for c in config.civilizations for civ_key in [c.key] for civ in [c] if civ_key == c.key}
    # spot checks of Drasah's verbatim V1 conditions.
    assert by_key["japonais"].requires_map_keys == ("archipel", "ile-volcanique")
    assert by_key["magyars"].display_name == "Hongrois"
    assert by_key["magyars"].requires_kingdom_name_pattern == "(?i).*hen.*"
    assert by_key["chinois"].max_map_count == 2
    assert by_key["romains"].min_map_count == 11
    assert by_key["armeniens"].requires_map_types == ("eau",) and by_key["armeniens"].min_map_type_count == 5
    assert by_key["teutons"].requires_map_types == ("mur_depart",) and by_key["teutons"].min_map_type_count == 4
    assert by_key["goths"].min_map_key_count == 4
    assert by_key["hindoustanis"].min_map_key_count == 3
    assert by_key["birmans"].requires_tech_points == 2
    assert by_key["bohemiens"].requires_marriages == 2
    assert by_key["portugais"].requires_civilization_count == 5
    assert by_key["shu"].requires_civilization == "chinois"
    assert by_key["serengeti".replace("serengeti", "ethiopiens")].requires_map_keys == ("serengeti",)


def _v1_config(civs: tuple[CivilizationCondition, ...]) -> KingdomsSeasonConfig:
    """A config with Drasah's new catalog and the tested conditions."""
    return KingdomsSeasonConfig(
        maps=default_map_catalog(),
        ages=(
            Epoch(key="dark_age", display_name="Âge sombre", gaia_ai_level=2, tech_points=0, extra_marriages=1),
        ),
        civilizations=civs,
    )


async def test_map_type_count_threshold_unlocks_only_at_n() -> None:
    """« N maps de type T »: the threshold, not the binary any-map."""
    bundle = Bundle(
        _v1_config(
            (
                CivilizationCondition(
                    key="berberes", display_name="Berbères",
                    requires_map_types=("ouverte",), min_map_type_count=2,
                ),
            )
        )
    )
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    # ONE Ouverte map is not enough.
    await bundle.draw_one(aquitaine, "lombardie")
    reports = await bundle.diplomacy.recalculate()
    assert "berberes" not in reports["Aquitaine"]
    # TWO Ouverte maps unlock.
    await bundle.draw_one(aquitaine, "serengeti")
    reports = await bundle.diplomacy.recalculate()
    assert "berberes" in reports["Aquitaine"]


async def test_map_key_count_threshold_unlocks_only_at_n() -> None:
    """Goths-style: at least 4 of the listed maps, not any single one."""
    bundle = Bundle(
        _v1_config(
            (
                CivilizationCondition(
                    key="goths", display_name="Goths",
                    requires_map_keys=("lac-glacial", "lac-alpin", "quatre-lacs", "la-cite-des-lacs"),
                    min_map_key_count=3,
                ),
            )
        )
    )
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    await bundle.draw_one(aquitaine, "lac-glacial")
    await bundle.draw_one(aquitaine, "lac-alpin")
    reports = await bundle.diplomacy.recalculate()
    assert "goths" not in reports["Aquitaine"]
    await bundle.draw_one(aquitaine, "quatre-lacs")
    reports = await bundle.diplomacy.recalculate()
    assert "goths" in reports["Aquitaine"]


async def test_map_count_min_and_max_thresholds() -> None:
    """Chinois < 3 maps, Romains > 10 maps — inclusive data thresholds."""
    bundle = Bundle(
        _v1_config(
            (
                CivilizationCondition(key="chinois", display_name="Chinois", max_map_count=2),
                CivilizationCondition(key="romains", display_name="Romains", min_map_count=3),
            )
        )
    )
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    await bundle.draw_one(aquitaine, "arabia")
    await bundle.draw_one(aquitaine, "islands")
    reports = await bundle.diplomacy.recalculate()
    assert "chinois" in reports["Aquitaine"]
    assert "romains" not in reports["Aquitaine"]
    await bundle.draw_one(aquitaine, "yucatan")
    reports = await bundle.diplomacy.recalculate()
    assert "chinois" not in reports["Aquitaine"]
    assert "romains" in reports["Aquitaine"]


async def test_tech_points_and_marriage_and_civ_count_conditions() -> None:
    """Birmans 2 ⚙️, Bohémiens 2 marriages, Portugais 5 civs."""
    bundle = Bundle(
        _v1_config(
            (
                CivilizationCondition(key="birmans", display_name="Birmans", requires_tech_points=2),
                CivilizationCondition(key="bohemiens", display_name="Bohémiens", requires_marriages=2),
                CivilizationCondition(key="portugais", display_name="Portugais", requires_civilization_count=2),
                CivilizationCondition(key="celtes", display_name="Celtes", requires_map_keys=("black-forest",)),
            )
        )
    )
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    # 1 tech point: not yet.
    await bundle.patch_kingdom_of("king-a", tech_points_bank=1)
    reports = await bundle.diplomacy.recalculate()
    assert "birmans" not in reports["Aquitaine"]
    await bundle.patch_kingdom_of("king-a", tech_points_bank=2)
    reports = await bundle.diplomacy.recalculate()
    assert "birmans" in reports["Aquitaine"]
    # one marriage is not two.
    await bundle.draw_one(aquitaine, "black-forest")
    await bundle.give("Aquitaine", "celtes")
    # a royal marriage (Reine) costs 2 marriage points.
    await bundle.patch_kingdom_of("king-a", marriage_points=2)
    await bundle.diplomacy.marry("king-a", "celtes")
    reports = await bundle.diplomacy.recalculate()
    assert "bohemiens" not in reports["Aquitaine"]
    await bundle.patch_kingdom_of("lord-a", marriage_points=1)
    await bundle.diplomacy.marry("lord-a", "birmans")
    reports = await bundle.diplomacy.recalculate()
    assert "bohemiens" in reports["Aquitaine"]
    # the kingdom now holds 2 civs: the Portugais threshold (2) is met.
    assert "portugais" in reports["Aquitaine"]


# ----------------------------------------------------------------------
# The removed-map migration
# ----------------------------------------------------------------------
async def test_removed_map_keys_are_migrated_onto_the_catalog() -> None:
    """A territory still referencing Altai lands on a real catalog map."""
    bundle = Bundle(_v1_config(()))
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    await bundle.draw_one(aquitaine, "arabia")
    # forge a v0-era territory pointing at a removed map.
    territory = next(t for t in await bundle.territories.territories() if t.map_key == "arabia")
    territory.map_key = "altai"
    await bundle.store.upsert_territory(territory.to_mongo())
    # the migration is one-shot per process and already ran while drawing:
    # rewind the flag so the next read exercises the remap.
    bundle.territories._map_migration_done = False
    # the first read triggers the one-shot migration.
    territories = await bundle.territories.territories()
    migrated = next(t for t in territories if t.id == territory.id)
    catalog = {entry.key for entry in default_map_catalog()}
    assert migrated.map_key in catalog
    assert migrated.map_key != "altai"
    # the second read is a no-op (the migration flag).
    assert await bundle.territories.migrate_removed_map_keys() == 0
