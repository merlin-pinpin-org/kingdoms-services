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


def test_banner_file_prefers_the_canonical_path_then_the_root_fallback(
    tmp_path: "object",
) -> None:
    """marche-banner-live-v2 (Drasah 29/10): the web upload landed at the
    repo ROOT as « marché.jpg » and the container imports the wheel —
    banner_file() tries the canonical assets path first, then the
    fallbacks (repo root via parents[4], workdir via cwd), and answers
    None when none exists (the panel works without the art)."""
    from pathlib import Path

    from kingdoms.mods.kingdoms import marche_art

    original = marche_art._BANNER_CANDIDATES
    try:
        canonical = Path(str(tmp_path) + "/assets/marche_banner.png")
        fallback = Path(str(tmp_path) + "/marché.jpg")
        marche_art._BANNER_CANDIDATES = (canonical, fallback)

        assert marche_art.banner_file() is None  # nothing uploaded yet

        fallback.parent.mkdir(parents=True, exist_ok=True)
        fallback.write_bytes(b"\xff\xd8fake")
        assert marche_art.banner_file() == fallback  # root fallback found

        canonical.parent.mkdir(parents=True, exist_ok=True)
        canonical.write_bytes(b"\x89PNGfake")
        assert marche_art.banner_file() == canonical  # canonical wins
    finally:
        marche_art._BANNER_CANDIDATES = original
