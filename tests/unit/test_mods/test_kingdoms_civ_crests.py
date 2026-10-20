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
    assert [f.filename for f in (live[0].files or [])] == ["azteques.png", "berberes.png"]

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
