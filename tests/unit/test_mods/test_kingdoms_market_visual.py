"""Marche-visuel-v2 (Drasah 28/10): one stall, four shelves, uniform
buttons — non-regression on the shelf taxonomy and its strings."""

from kingdoms.mods.kingdoms import kingdom_panels
from kingdoms.mods.kingdoms.kingdom_panels import MARKET_SECTIONS


def test_market_sections_follow_drasah_order() -> None:
    assert [header for header, _ in MARKET_SECTIONS] == [
        "market_section_ia",
        "market_section_defense",
        "market_section_attaque",
        "market_section_speciales",
    ]


def test_all_market_items_are_shelved_exactly_once() -> None:
    keys = [key for _, shelf in MARKET_SECTIONS for key in shelf]
    assert len(keys) == len(set(keys)) == 10


def test_section_and_crier_strings_exist_in_both_locales() -> None:
    for locale in ("fr", "en"):
        strings = kingdom_panels._strings(locale)
        assert strings["market_crier"]
        for header, _ in MARKET_SECTIONS:
            assert strings[header], (locale, header)
