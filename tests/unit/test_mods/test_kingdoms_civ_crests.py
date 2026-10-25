"""civ-crests-v1 (Drasah 20/10) — blasons on the civ lists.

The player-facing civilization lists wear their crests: the 📜
Alliances draft starter, the pick & ban REVEAL DM and the
battle-room meeting message attach the civs' crest PNGs under the
text. The mapping covers EVERY declared season.yaml civilization, a
failed download never blocks the message, and the display order of
the reveal stays shuffled (the ranking stays secret).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.mocks.discord_mock import MockGuild

REPO_ROOT = Path(__file__).resolve().parents[3]


# ----------------------------------------------------------------------
# The mapping — every declared civilization has a crest
# ----------------------------------------------------------------------
def test_every_season_civilization_has_a_crest_mapping() -> None:
    """All 55 season.yaml civ keys map to a dataset civ (crest exists)."""
    from kingdoms.mods.kingdoms.civ_crests import _CIV_CREST_PROVIDER_KEYS, civ_crest_url
    from kingdoms.mods.kingdoms.config import load_season_config

    config = load_season_config(REPO_ROOT / "config")
    declared = {civ.key for civ in config.civilizations}
    assert declared
    assert declared == set(_CIV_CREST_PROVIDER_KEYS), declared.symmetric_difference(
        set(_CIV_CREST_PROVIDER_KEYS)
    )
    dataset = json.loads(
        (REPO_ROOT / "data" / "core" / "aoe2techtree" / "data.json").read_text(encoding="utf-8")
    )
    known = set(dataset.get("civs") or {})
    for key in declared:
        assert civ_crest_url(key) is not None, f"{key} has no crest URL"
        assert _CIV_CREST_PROVIDER_KEYS[key] in known, f"{key} maps to an unknown dataset civ"
    assert civ_crest_url("civ-inexistante") is None


# ----------------------------------------------------------------------
# civ_crest_files — download seam, order, dedupe, resilience
# ----------------------------------------------------------------------
async def test_crest_files_preserve_order_and_dedupe(monkeypatch: pytest.MonkeyPatch) -> None:
    from kingdoms.mods.kingdoms import civ_crests

    downloads: list[str] = []

    async def fake_crest_bytes(url: str) -> bytes:
        downloads.append(url)
        return f"png:{url}".encode()

    monkeypatch.setattr(civ_crests, "_crest_bytes", fake_crest_bytes)
    files = await civ_crests.civ_crest_files(["berberes", "azteques", "berberes", ""])
    assert len(files) == 2
    assert [f.filename for f in files] == ["berberes.png", "azteques.png"]
    assert downloads == [
        civ_crests.civ_crest_url("berberes"),
        civ_crests.civ_crest_url("azteques"),
    ]


async def test_crest_files_skip_failed_downloads_and_unknown_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kingdoms.mods.kingdoms import civ_crests

    async def fake_crest_bytes(url: str) -> bytes | None:
        return None if "aztecs" in url else b"png"

    monkeypatch.setattr(civ_crests, "_crest_bytes", fake_crest_bytes)
    files = await civ_crests.civ_crest_files(["berberes", "azteques", "civ-inexistante"])
    assert [f.filename for f in files] == ["berberes.png"]


async def test_crest_files_cap_at_ten(monkeypatch: pytest.MonkeyPatch) -> None:
    from kingdoms.mods.kingdoms import civ_crests

    async def fake_crest_bytes(url: str) -> bytes:
        return b"png"

    monkeypatch.setattr(civ_crests, "_crest_bytes", fake_crest_bytes)
    keys = list(civ_crests._CIV_CREST_PROVIDER_KEYS)[:15]
    files = await civ_crests.civ_crest_files(keys)
    assert len(files) == civ_crests.MAX_ATTACHMENTS == 10


# ----------------------------------------------------------------------
# The 📜 Alliances draft starter wears the hand's crests
# ----------------------------------------------------------------------
async def test_draft_starter_attaches_the_hand_crests(monkeypatch: pytest.MonkeyPatch) -> None:
    from kingdoms.mods.kingdoms import civ_crests
    from kingdoms.mods.kingdoms.kingdom_realms import (
        DRAFT_STARTER_MARKER,
        announce_draft_starter,
        ensure_realm_structure,
    )

    async def fake_crest_bytes(url: str) -> bytes:
        return b"png"

    monkeypatch.setattr(civ_crests, "_crest_bytes", fake_crest_bytes)

    class _Civ:
        def __init__(self, key: str, display_name: str) -> None:
            self.key = key
            self.display_name = display_name

    class _Config:
        civilizations = (_Civ("azteques", "Aztèques"), _Civ("berberes", "Berbères"))

    class _Service:
        config = _Config()

    class _Kingdom:
        id = "k-1"
        name = "Avalon"
        validation = "approved"
        is_gaia = False
        civilizations = ["azteques", "berberes"]  # noqa: RUF012

    guild = MockGuild()
    await ensure_realm_structure(guild, _Kingdom(), [])
    assert await announce_draft_starter(guild, _Kingdom(), _Service()) is True
    alliances = next(c for c in guild.categories[0].channels if c.name == "🤝 alliances")
    live = [m for m in alliances.messages if not getattr(m, "deleted", False)]
    assert len(live) == 1
    assert "Draft starter" in live[0].content and DRAFT_STARTER_MARKER in live[0].content
    # alliances-lisibilite-v1 T3: ONE composite mosaic, not N full-size PNGs
    assert [f.filename for f in (live[0].files or [])] == ["draft-starter.png"]

    # a refresh edits the marked message — still one message, crests re-attached
    assert await announce_draft_starter(guild, _Kingdom(), _Service()) is True
    live = [m for m in alliances.messages if not getattr(m, "deleted", False)]
    assert len(live) == 1


# ----------------------------------------------------------------------
# The pick & ban REVEAL DM wears the enemy civs' crests
# ----------------------------------------------------------------------
async def test_pickban_reveal_dm_attaches_enemy_crests(monkeypatch: pytest.MonkeyPatch) -> None:
    from kingdoms.mods.kingdoms import civ_crests
    from kingdoms.mods.kingdoms.kingdom_attack_panel import _edit_pickban_dms_reveal

    async def fake_crest_bytes(url: str) -> bytes:
        return b"png"

    monkeypatch.setattr(civ_crests, "_crest_bytes", fake_crest_bytes)

    class _Attack:
        id = "a-1"
        attacker_lord_id = "111"
        defender_lord_id = "222"
        ai_defender = False
        map_key = "arabia"
        pickban = {  # noqa: RUF012
            "attacker_picks": ["francs", "goths"],
            "defender_picks": ["celtes", "huns"],
            "attacker_dm": {},  # no stored ref → new DM fallback
            "defender_dm": {},
        }

    class _User:
        name = "TestUser"

        async def send(self, content: str | None = None, **kwargs: object) -> object:
            class _Msg:
                content = content
                files = kwargs.get("files")

            return _Msg()

    class _Bot:
        guilds: list[object] = []  # noqa: RUF012

        def get_user(self, user_id: str) -> object:
            return _User()

        async def fetch_user(self, user_id: int) -> object:
            return _User()

    sent: dict[str, list[str]] = {}

    async def _dm(bot: object, user_id: str, content: str, view: object = None, files: object = None) -> None:
        sent[user_id] = [f.filename for f in (files or [])]  # type: ignore[attr-defined]

    import kingdoms.mods.kingdoms.kingdom_attack_panel as panel

    monkeypatch.setattr(panel, "_dm_user_safe", _dm)
    await _edit_pickban_dms_reveal(_Bot(), _Attack(), None, panel._strings("fr"))
    # the attacker sees the DEFENDER's picks (shuffled order kept, not the ranking)
    assert sorted(sent["111"]) == ["celtes.png", "huns.png"]
    assert sorted(sent["222"]) == ["francs.png", "goths.png"]


# ----------------------------------------------------------------------
# alliances-lisibilite-v1 T3 (Drasah 25/10) — the crest MOSAIC
# ----------------------------------------------------------------------
def _tiny_colored_png(red: int, green: int, blue: int) -> bytes:
    """One solid 4×4 RGBA PNG (the mosaic diet, no network)."""
    from kingdoms.mods.kingdoms.civ_crests import _encode_png_rgba

    pixels = b"".join(bytes((red, green, blue, 255)) for _ in range(16))
    return _encode_png_rgba(4, 4, pixels)


def _tiny_red_png() -> bytes:
    return _tiny_colored_png(255, 0, 0)


def _file_bytes(file: object) -> bytes:
    """Read one discord.File's payload back (the mosaic's pixels)."""
    fp = getattr(file, "fp")
    fp.seek(0)
    return bytes(fp.read())


async def test_mosaic_file_wears_one_emoji_badge_per_tile(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every tile carries its crest and its ORIGIN EMOJI badge — ONE attachment."""
    from kingdoms.mods.kingdoms import civ_crests
    from kingdoms.mods.kingdoms.civ_crests import (
        DEFAULT_EMOJI_CODE,
        MOSAIC_BADGE,
        MOSAIC_BADGE_INSET,
        MOSAIC_COLS,
        MOSAIC_TILE,
        ORIGIN_EMOJI_CODES,
        _decode_png_rgba,
        civ_crest_mosaic_file,
    )

    async def fake_crest_bytes(url: str) -> bytes:
        return _tiny_red_png()

    codes: list[str] = []

    async def fake_emoji_bytes(code: str) -> bytes:
        codes.append(code)
        return _tiny_colored_png(0, 0, 255)  # a blue "emoji glyph"

    monkeypatch.setattr(civ_crests, "_crest_bytes", fake_crest_bytes)
    monkeypatch.setattr(civ_crests, "_emoji_bytes", fake_emoji_bytes)

    # 3 civs (the duplicated francs is dropped), mixed origins
    file = await civ_crest_mosaic_file(
        [("francs", "starter"), ("huns", "marriage"), ("goths", ""), ("francs", "royal")]
    )
    assert file is not None and file.filename == "alliances.png"
    decoded = _decode_png_rgba(_file_bytes(file))
    assert decoded is not None
    width, height, pixels = decoded
    assert (width, height) == (MOSAIC_COLS * MOSAIC_TILE, 1 * MOSAIC_TILE)

    def pixel(x: int, y: int) -> tuple[int, int, int]:
        dest = (y * width + x) * 4
        return (pixels[dest], pixels[dest + 1], pixels[dest + 2])

    def badge_center(index: int) -> tuple[int, int]:
        col = index % MOSAIC_COLS
        return (
            col * MOSAIC_TILE + MOSAIC_TILE - MOSAIC_BADGE - MOSAIC_BADGE_INSET + MOSAIC_BADGE // 2,
            MOSAIC_TILE - MOSAIC_BADGE - MOSAIC_BADGE_INSET + MOSAIC_BADGE // 2,
        )

    # mosaic-emoji-badge: the badge is the origin EMOJI glyph, not a colour square
    assert pixel(*badge_center(0)) == (0, 0, 255)
    assert pixel(*badge_center(1)) == (0, 0, 255)
    assert pixel(*badge_center(2)) == (0, 0, 255)  # unknown origin → 🤝 glyph
    assert codes == [
        ORIGIN_EMOJI_CODES["starter"],
        ORIGIN_EMOJI_CODES["marriage"],
        DEFAULT_EMOJI_CODE,
    ]  # the duplicated francs tile is dropped before its badge draw
    # the crest itself is resampled onto the tile (caption strip above it)
    assert pixel(12 + 52, 24 + 52) == (255, 0, 0)


async def test_mosaic_tile_wears_the_civ_name_caption(monkeypatch: pytest.MonkeyPatch) -> None:
    """mosaic-name-captions: each tile's NAME rides a dark strip above the crest."""
    from kingdoms.mods.kingdoms import civ_crests
    from kingdoms.mods.kingdoms.civ_crests import (
        MOSAIC_CAPTION_H,
        MOSAIC_CAPTION_Y,
        MOSAIC_TILE,
        _decode_png_rgba,
        civ_crest_mosaic_file,
    )

    async def fake_crest_bytes(url: str) -> bytes:
        return _tiny_red_png()

    async def fake_emoji_bytes(code: str) -> bytes | None:
        return None

    monkeypatch.setattr(civ_crests, "_crest_bytes", fake_crest_bytes)
    monkeypatch.setattr(civ_crests, "_emoji_bytes", fake_emoji_bytes)

    # a NAMED civ wears its caption strip; a legacy 2-tuple wears none
    file = await civ_crest_mosaic_file(
        [("francs", "starter", "Francs"), ("huns", "marriage")]
    )
    assert file is not None
    decoded = _decode_png_rgba(_file_bytes(file))
    assert decoded is not None
    width, _height, pixels = decoded

    def pixel(x: int, y: int) -> tuple[int, int, int]:
        dest = (y * width + x) * 4
        return (pixels[dest], pixels[dest + 1], pixels[dest + 2])

    # tile 0 (named): the strip is attached above the crest — dark, with
    # white glyph pixels; the accented name renders (È = E + grave)
    assert pixel(6, MOSAIC_CAPTION_Y + 3) == (22, 24, 29)
    whites = 0
    for y in range(MOSAIC_CAPTION_Y, MOSAIC_CAPTION_Y + MOSAIC_CAPTION_H):
        for x in range(MOSAIC_TILE):
            if pixel(x, y) == (245, 246, 248):
                whites += 1
    assert whites > 20, "the name must be painted on the caption strip"
    # tile 1 (legacy 2-tuple): no caption strip — the tile stays transparent
    assert pixel(MOSAIC_TILE + 6, MOSAIC_CAPTION_Y + 3) == (0, 0, 0)


async def test_mosaic_badge_falls_back_to_the_colour_square(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed emoji fetch (offline) still marks provenance: colour square."""
    from kingdoms.mods.kingdoms import civ_crests
    from kingdoms.mods.kingdoms.civ_crests import (
        MOSAIC_BADGE,
        MOSAIC_BADGE_INSET,
        MOSAIC_COLS,
        MOSAIC_TILE,
        ORIGIN_BADGE_COLORS,
        _decode_png_rgba,
        civ_crest_mosaic_file,
    )

    async def fake_crest_bytes(url: str) -> bytes:
        return _tiny_red_png()

    async def fake_emoji_bytes(code: str) -> bytes | None:
        return None

    monkeypatch.setattr(civ_crests, "_crest_bytes", fake_crest_bytes)
    monkeypatch.setattr(civ_crests, "_emoji_bytes", fake_emoji_bytes)
    file = await civ_crest_mosaic_file([("francs", "starter")])
    assert file is not None
    decoded = _decode_png_rgba(_file_bytes(file))
    assert decoded is not None
    width, _height, pixels = decoded
    x = MOSAIC_TILE - MOSAIC_BADGE - MOSAIC_BADGE_INSET + MOSAIC_BADGE // 2
    y = MOSAIC_TILE - MOSAIC_BADGE - MOSAIC_BADGE_INSET + MOSAIC_BADGE // 2
    dest = (y * width + x) * 4
    assert (pixels[dest], pixels[dest + 1], pixels[dest + 2]) == ORIGIN_BADGE_COLORS["starter"]


async def test_mosaic_file_survives_undecodable_pngs(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed download or a bogus PNG falls back to the default crest."""
    from kingdoms.mods.kingdoms import civ_crests
    from kingdoms.mods.kingdoms.civ_crests import _decode_png_rgba, civ_crest_mosaic_file

    async def fake_crest_bytes(url: str) -> bytes | None:
        return None

    monkeypatch.setattr(civ_crests, "_crest_bytes", fake_crest_bytes)
    file = await civ_crest_mosaic_file([("azteques", "starter")])
    assert file is not None
    assert _decode_png_rgba(_file_bytes(file)) is not None


async def test_mosaic_file_returns_none_for_an_empty_hand() -> None:
    """An empty hand sends NO attachment at all."""
    from kingdoms.mods.kingdoms.civ_crests import civ_crest_mosaic_file

    assert await civ_crest_mosaic_file([]) is None
    assert await civ_crest_mosaic_file([("", "starter")]) is None
