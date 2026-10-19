"""Mod declaration privacy: private mods and their owners (roster ids)."""

from __future__ import annotations

from pathlib import Path

import pytest

from kingdoms.core.services.mod_registry import load_mod_definitions


def _write(tmp_path: Path, body: str) -> Path:
    mods = tmp_path / "mods"
    mods.mkdir()
    (mods / "kingdoms.yaml").write_text(body, encoding="utf-8")
    return tmp_path


PUBLIC = """id: kingdoms
enabled: true
seasonal: false
channels: []
roles: []
"""

PRIVATE = """id: kingdoms
enabled: true
seasonal: false
private: true
owner_discord_ids:
  - "111222333444555666"
  - "999888777666555444"
channels: []
roles: []
"""


def test_public_mod_has_no_owners(tmp_path: Path) -> None:
    defs = load_mod_definitions(_write(tmp_path, PUBLIC))
    definition = defs["kingdoms"]
    assert not definition.is_private()
    assert definition.owner_ids == ()


def test_private_mod_declares_owners(tmp_path: Path) -> None:
    defs = load_mod_definitions(_write(tmp_path, PRIVATE))
    definition = defs["kingdoms"]
    assert definition.is_private()
    assert definition.owned_by("111222333444555666")
    assert definition.owned_by("999888777666555444")
    assert not definition.owned_by("000000000000000000")
    assert not definition.owned_by("111222333444555667")


def test_private_mod_without_owners_fails_loudly() -> None:
    mods = Path("/tmp/km-broken/mods")
    if mods.exists():
        for stale in mods.iterdir():
            stale.unlink()
    mods.mkdir(parents=True, exist_ok=True)
    (mods / "kingdoms.yaml").write_text(
        PRIVATE.replace('  - "111222333444555666"\n', "").replace('  - "999888777666555444"\n', ""),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="owner_discord_ids"):
        load_mod_definitions(mods.parent)
