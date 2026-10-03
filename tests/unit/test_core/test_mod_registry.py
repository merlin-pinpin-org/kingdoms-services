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


# ---------------------------------------------------------------------------
# Channel groups, kinds and adopt policies (kingdoms-services#175)
# ---------------------------------------------------------------------------


def _write_mod(tmp_path: "Path", body: str) -> None:
    mods_dir = tmp_path / "mods"
    mods_dir.mkdir(exist_ok=True)
    (mods_dir / "shiny.yaml").write_text(body)


GROUPED_MOD = (
    "id: shiny\n"
    "channel_groups:\n"
    "  - key: main\n"
    "    display_name: Salons\n"
    "  - key: admin\n"
    "    display_name: Admin\n"
    "    admin_only: true\n"
    "channels:\n"
    "  - key: announce\n"
    "    display_name: Annonces\n"
    "    group: main\n"
    "    kind: announce\n"
    "  - key: rules\n"
    "    display_name: Règles\n"
    "    group: main\n"
    "    kind: forum\n"
    "  - key: epoch\n"
    "    display_name: Âge sombre\n"
    "    group: main\n"
    "    adopt: group_single\n"
    "  - key: requests\n"
    "    display_name: Demandes\n"
    "    group: admin\n"
    "    admin_only: true\n"
)


def test_channel_groups_are_parsed_as_data(tmp_path: Path) -> None:
    """Groups carry declaration order (position) and admin-only flags."""
    _write_mod(tmp_path, GROUPED_MOD)
    definition = load_mod_definitions(tmp_path)["shiny"]

    assert [group.key for group in definition.channel_groups] == ["main", "admin"]
    assert definition.channel_groups[0].position == 0
    assert definition.channel_groups[1].position == 1
    assert definition.channel_groups[1].admin_only is True
    assert definition.channel_group("admin").admin_only is True


def test_channel_kinds_and_adopt_policies_are_parsed(tmp_path: Path) -> None:
    """Channels declare their group, kind, admin-only flag and adopt policy."""
    _write_mod(tmp_path, GROUPED_MOD)
    definition = load_mod_definitions(tmp_path)["shiny"]

    announce = definition.channel_category("announce")
    assert announce.group == "main" and announce.kind == "announce"
    assert definition.channel_category("rules").kind == "forum"
    assert definition.channel_category("epoch").adopt == "group_single"
    assert definition.channel_category("requests").admin_only is True
    assert [category.key for category in definition.channels_of_group("main")] == [
        "announce",
        "rules",
        "epoch",
    ]


def test_channels_of_group_are_positioned_in_declaration_order(tmp_path: Path) -> None:
    """Positions are per group, assigned in declaration order."""
    _write_mod(tmp_path, GROUPED_MOD)
    definition = load_mod_definitions(tmp_path)["shiny"]

    positions = {category.key: category.position for category in definition.channel_categories}
    assert positions == {"announce": 0, "rules": 1, "epoch": 2, "requests": 0}


def test_undeclared_group_reference_fails_loudly(tmp_path: Path) -> None:
    """A channel may only reference a declared group."""
    _write_mod(
        tmp_path,
        "id: shiny\n"
        "channels:\n"
        "  - key: announce\n"
        "    display_name: Annonces\n"
        "    group: ghost\n",
    )
    with pytest.raises(ValueError, match="undeclared group 'ghost'"):
        load_mod_definitions(tmp_path)


def test_unknown_channel_kind_fails_loudly(tmp_path: Path) -> None:
    """Kinds are a closed set (text/forum/announce)."""
    _write_mod(
        tmp_path,
        "id: shiny\n"
        "channels:\n"
        "  - key: announce\n"
        "    display_name: Annonces\n"
        "    kind: voice\n",
    )
    with pytest.raises(ValueError, match="unknown kind 'voice'"):
        load_mod_definitions(tmp_path)


def test_unknown_adopt_policy_fails_loudly(tmp_path: Path) -> None:
    """Adopt policies are a closed set (name/group_single)."""
    _write_mod(
        tmp_path,
        "id: shiny\n"
        "channels:\n"
        "  - key: announce\n"
        "    display_name: Annonces\n"
        "    adopt: always\n",
    )
    with pytest.raises(ValueError, match="unknown adopt policy 'always'"):
        load_mod_definitions(tmp_path)


def test_channel_groups_must_be_a_list(tmp_path: Path) -> None:
    """A malformed channel_groups section fails startup loudly."""
    _write_mod(tmp_path, "id: shiny\nchannel_groups: nope\n")
    with pytest.raises(ValueError, match="'channel_groups' must be a list"):
        load_mod_definitions(tmp_path)


def test_channel_group_lookup_fails_loudly_for_undeclared() -> None:
    definition = make_definition()
    with pytest.raises(KeyError, match="does not declare channel group"):
        definition.channel_group("nope")
