"""Channel groups, kinds and the admin_only/read_only flags (the salons-as-data declaration)."""

from __future__ import annotations

from pathlib import Path

import pytest

from kingdoms.core.services.mod_registry import load_mod_definitions


def _mod(tmp_path: Path, body: str) -> object:
    """Declare one legacy mod and return its definition."""
    mods = tmp_path / "mods"
    mods.mkdir()
    (mods / "example.yaml").write_text(body, encoding="utf-8")
    return load_mod_definitions(tmp_path)["example"]


ROLES = """
roles:
  - key: lord
    display_name: Kingdoms Lord
  - key: king
    display_name: Kingdoms King
  - key: admin
    display_name: Kingdoms Admin
    admin: true
"""


def test_groups_parse_with_display_order(tmp_path: Path) -> None:
    """The declaration order is the display order."""
    definition = _mod(
        tmp_path,
        f"""id: example
seasonal: false
{ROLES}
channel_groups:
  - key: conscription
    display_name: ✒️ Conscription
  - key: kingdoms
    display_name: 🏰 Kingdoms
  - key: admin
    display_name: ⚙️ Admin
    admin_only: true
channels: []
""",
    )
    assert [g.key for g in definition.channel_groups] == ["conscription", "kingdoms", "admin"]
    assert [g.position for g in definition.channel_groups] == [0, 1, 2]
    assert definition.channel_groups[2].admin_only is True


def test_channel_inherits_group_flags(tmp_path: Path) -> None:
    """Unset flags inherit the group's; admin_only resolves the admin roles."""
    definition = _mod(
        tmp_path,
        f"""id: example
seasonal: false
{ROLES}
channel_groups:
  - key: admin
    display_name: ⚙️ Admin
    admin_only: true
channels:
  - key: ops
    display_name: 🛠 opérations
    group: admin
""",
    )
    (channel,) = definition.channel_categories
    assert channel.admin_only is True
    # the audience is the mod's admin roles, @everyone denied
    assert channel.access.view == ("Kingdoms Admin",)
    assert channel.access.everyone_view is False


def test_read_only_collapses_post(tmp_path: Path) -> None:
    """read_only: nobody writes — the post policies collapse."""
    definition = _mod(
        tmp_path,
        f"""id: example
seasonal: false
{ROLES}
channel_groups:
  - key: geopolitics
    display_name: 🌍 Géopolitique
    read_only: true
channels:
  - key: annonces
    display_name: 📢 annonces
    group: geopolitics
    kind: announce
""",
    )
    (channel,) = definition.channel_categories
    assert channel.read_only is True
    assert channel.access.post == ()
    assert channel.access.everyone_post is False
    assert channel.kind == "announce"


def test_announce_channel_is_read_only_by_default(tmp_path: Path) -> None:
    """An announce channel (admin-written, everyone-readable) defaults read-only."""
    definition = _mod(
        tmp_path,
        f"""id: example
seasonal: false
{ROLES}
channel_groups:
  - key: general
    display_name: Général
channels:
  - key: annonces
    display_name: 📢 annonces
    kind: announce
""",
    )
    (channel,) = definition.channel_categories
    assert channel.read_only is True
    assert channel.kind == "announce"


def test_access_view_entries_resolve_role_keys(tmp_path: Path) -> None:
    """view/post reference the mod's role keys — resolved to the declared names."""
    definition = _mod(
        tmp_path,
        f"""id: example
seasonal: false
{ROLES}
channel_groups:
  - key: kingdoms
    display_name: 🏰 Kingdoms
channels:
  - key: market
    display_name: 💰 marché
    group: kingdoms
    access:
      everyone_view: false
      view: [lord, king]
      post: [lord, king]
""",
    )
    (channel,) = definition.channel_categories
    assert channel.access.view == ("Kingdoms Lord", "Kingdoms King")
    assert channel.access.post == ("Kingdoms Lord", "Kingdoms King")
    assert channel.access.everyone_view is False


def test_undeclared_group_fails_loudly(tmp_path: Path) -> None:
    """A channel referencing an undeclared group never loads half-initialized."""
    with pytest.raises(ValueError, match="undeclared group"):
        _mod(
            tmp_path,
            f"""id: example
seasonal: false
{ROLES}
channels:
  - key: market
    display_name: 💰 marché
    group: nowhere
""",
        )


def test_unknown_kind_fails_loudly(tmp_path: Path) -> None:
    """An unknown channel kind never loads."""
    with pytest.raises(ValueError, match="unknown kind"):
        _mod(
            tmp_path,
            f"""id: example
seasonal: false
{ROLES}
channels:
  - key: market
    display_name: 💰 marché
    kind: voice
""",
        )


def test_admin_only_channel_without_admin_role_fails_loudly(tmp_path: Path) -> None:
    """admin_only resolves to the mod's admin roles — a mod without one never loads."""
    with pytest.raises(ValueError, match="admin_only"):
        _mod(
            tmp_path,
            """id: example
seasonal: false
roles:
  - key: lord
    display_name: Kingdoms Lord
channel_groups:
  - key: admin
    display_name: ⚙️ Admin
    admin_only: true
channels:
  - key: ops
    display_name: 🛠 opérations
    group: admin
""",
        )
