"""Unit tests for the generic mod declaration tools (kingdoms-services#26)."""

from __future__ import annotations

from pathlib import Path

import pytest

from kingdoms.core.services.mod_definition import (
    ChannelCategoryDef,
    ModDefinition,
    RoleDef,
)
from kingdoms.core.services.mod_registry import ModRegistry, load_mod_definitions


def make_definition(name: str = "example") -> ModDefinition:
    return ModDefinition(
        name=name,
        channel_categories=(ChannelCategoryDef(key="announce", display_name="Annonces"),),
        roles=(RoleDef(key="member", display_name="Example Member"),),
    )


def test_mod_definition_lookup_finds_declared_entries() -> None:
    definition = make_definition()
    assert definition.channel_category("announce").display_name == "Annonces"
    assert definition.role("member").display_name == "Example Member"


def test_mod_definition_lookup_fails_loudly_for_undeclared() -> None:
    definition = make_definition()
    with pytest.raises(KeyError, match="does not declare channel category"):
        definition.channel_category("nope")
    with pytest.raises(KeyError, match="does not declare role"):
        definition.role("nope")


def test_registry_register_get_require_enabled() -> None:
    registry = ModRegistry()
    registry.register(make_definition())
    assert registry.get("example") is not None
    assert registry.require("example").name == "example"
    assert list(registry.enabled()) == ["example"]
    with pytest.raises(KeyError, match="not declared"):
        registry.require("missing")


def test_registry_disabled_mods_are_not_enabled() -> None:
    registry = ModRegistry({disabled.name: disabled for disabled in [ModDefinition(name="off", enabled=False)]})
    assert registry.get("off") is not None
    assert registry.enabled() == {}
    assert registry.all() == {"off": registry.get("off")}


def test_load_mod_definitions_validates_schema(tmp_path: Path) -> None:
    mods_dir = tmp_path / "mods"
    mods_dir.mkdir()
    (mods_dir / "good.yaml").write_text(
        "id: good\n"
        "enabled: true\n"
        "channels:\n"
        "  - key: announce\n"
        "    display_name: Annonces\n"
        "roles:\n"
        "  - key: member\n"
        "    display_name: Member\n",
        encoding="utf-8",
    )
    mods = load_mod_definitions(tmp_path)
    assert mods["good"].channel_category("announce").display_name == "Annonces"
    assert mods["good"].role("member").display_name == "Member"


def test_load_mod_definitions_fails_loudly_on_invalid(tmp_path: Path) -> None:
    mods_dir = tmp_path / "mods"
    mods_dir.mkdir()
    (mods_dir / "bad.yaml").write_text("id: bad\nchannels:\n  - key: no_display\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"key.*and.*display_name"):
        load_mod_definitions(tmp_path)


def test_load_mod_definitions_rejects_mod_scoped_slugs(tmp_path: Path) -> None:
    mods_dir = tmp_path / "mods"
    mods_dir.mkdir()
    (mods_dir / "weird.yaml").write_text("id: bad:slug\n", encoding="utf-8")
    with pytest.raises(ValueError, match="simple slug"):
        load_mod_definitions(tmp_path)


def test_load_mod_definitions_empty_dir(tmp_path: Path) -> None:
    assert load_mod_definitions(tmp_path) == {}
