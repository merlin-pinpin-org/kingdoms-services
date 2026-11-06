"""Unit tests for the modular mod layout resolution (kingdoms#178)."""

from __future__ import annotations

from pathlib import Path

import pytest

from kingdoms.core.services.mod_layout import discover_mods, find_mod_declaration


def test_modular_declaration_is_found(tmp_path: Path) -> None:
    mod_dir = tmp_path / "modules" / "mods" / "demo"
    (mod_dir / "src" / "demo").mkdir(parents=True)
    (mod_dir / "locales").mkdir()
    (mod_dir / "mod.yaml").write_text("id: demo\nseasonal: false\n")
    layout = find_mod_declaration(tmp_path, "demo")
    assert layout.modular
    assert layout.declaration == mod_dir / "mod.yaml"
    assert layout.package == mod_dir / "src" / "demo"
    assert layout.locale_files == ()


def test_modular_locale_files_are_collected(tmp_path: Path) -> None:
    mod_dir = tmp_path / "modules" / "mods" / "demo"
    (mod_dir / "locales").mkdir(parents=True)
    (mod_dir / "locales" / "en.yaml").write_text("en: {}\n")
    (mod_dir / "locales" / "fr.yaml").write_text("fr: {}\n")
    (mod_dir / "mod.yaml").write_text("id: demo\nseasonal: false\n")
    layout = find_mod_declaration(tmp_path, "demo")
    assert [p.name for p in layout.locale_files] == ["en.yaml", "fr.yaml"]


def test_legacy_declaration_takes_precedence(tmp_path: Path) -> None:
    legacy = tmp_path / "config" / "mods"
    legacy.mkdir(parents=True)
    (legacy / "demo.yaml").write_text("id: demo\nseasonal: true\n")
    modular = tmp_path / "modules" / "mods" / "demo"
    modular.mkdir(parents=True)
    (modular / "mod.yaml").write_text("id: demo\nseasonal: false\n")
    layout = find_mod_declaration(tmp_path, "demo")
    assert not layout.modular
    assert layout.declaration == legacy / "demo.yaml"


def test_missing_declaration_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="not declared"):
        find_mod_declaration(tmp_path, "ghost")


def test_discover_finds_both_layouts(tmp_path: Path) -> None:
    legacy = tmp_path / "config" / "mods"
    legacy.mkdir(parents=True)
    (legacy / "legacyone.yaml").write_text("id: legacyone\nseasonal: false\n")
    modular = tmp_path / "modules" / "mods" / "modularone"
    (modular / "src" / "modularone").mkdir(parents=True)
    (modular / "mod.yaml").write_text("id: modularone\nseasonal: true\n")
    (tmp_path / "modules" / "mods" / "not_a_mod").mkdir()
    layouts = discover_mods(tmp_path)
    assert set(layouts) == {"legacyone", "modularone"}
    assert layouts["legacyone"].modular is False
    assert layouts["modularone"].modular
