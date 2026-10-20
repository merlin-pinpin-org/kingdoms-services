"""Unit tests for `kingdoms mod init` — the mod scaffold generator."""

from __future__ import annotations

from pathlib import Path

import pytest

from kingdoms.core.services.mod_init import init_mod
from kingdoms.core.services.mod_layout import find_mod_declaration
from kingdoms.core.services.mod_registry import _load_declaration


def test_init_generates_the_full_mod_directory(tmp_path: Path) -> None:
    result = init_mod(tmp_path, "demo", seasonal=True)
    assert result.declaration.is_file()
    assert result.package.is_file()
    assert all(p.is_file() for p in result.locales)
    assert result.test.is_file()
    assert result.readme.is_file()
    assert result.mod_dir == tmp_path / "modules" / "mods" / "demo"


def test_generated_declaration_passes_schema_validation(tmp_path: Path) -> None:
    init_mod(tmp_path, "demo", seasonal=True)
    definition = _load_declaration(tmp_path / "modules" / "mods" / "demo" / "mod.yaml")
    assert definition.name == "demo"
    assert definition.enabled
    assert definition.seasonal
    assert definition.channel_category("announce").display_name
    assert definition.role("demo_member")


def test_generated_permanent_declaration(tmp_path: Path) -> None:
    init_mod(tmp_path, "demo", seasonal=False)
    definition = _load_declaration(tmp_path / "modules" / "mods" / "demo" / "mod.yaml")
    assert not definition.seasonal


def test_generated_layout_resolves_as_modular(tmp_path: Path) -> None:
    init_mod(tmp_path, "demo", seasonal=True)
    layout = find_mod_declaration(tmp_path, "demo")
    assert layout.modular
    assert layout.package is not None and layout.package.is_dir()


def test_generated_locale_files_load(tmp_path: Path) -> None:
    init_mod(tmp_path, "demo", seasonal=True)
    for path in (tmp_path / "modules" / "mods" / "demo" / "locales").glob("*.yaml"):
        assert path.read_text(encoding="utf-8").startswith(path.stem)


def test_init_refuses_existing_mod(tmp_path: Path) -> None:
    init_mod(tmp_path, "demo", seasonal=True)
    with pytest.raises(FileExistsError, match="already exists"):
        init_mod(tmp_path, "demo", seasonal=True)


def test_init_refuses_invalid_slug(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="slug"):
        init_mod(tmp_path, "not:a:slug", seasonal=True)
