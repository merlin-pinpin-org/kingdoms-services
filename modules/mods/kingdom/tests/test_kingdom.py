"""kingdom mod — scaffold acceptance test (MockDiscord, in-memory stores)."""

from __future__ import annotations


def test_kingdom_cog_package_loads() -> None:
    """The generated cog package imports against the core seams."""
    from botforge.mods.kingdom import cogs  # noqa: F401


def test_kingdom_service_implements_the_contract() -> None:
    """The service package exposes the contract's implementation."""
    from botforge.mods.kingdom.grpc import KingdomServiceImpl
    from botforge.mods.kingdom.grpc.contract import KingdomService

    impl = KingdomServiceImpl()
    assert isinstance(impl, KingdomService)


def test_kingdom_declaration_autoloads_uninstalled_by_default() -> None:
    """The mod is discovered from the repo root and declares its game.

    default_active: false — the mods catalog shows it NOT installed by
    default (games are the inverse); required_games: aoe2 — installing
    kingdom installs aoe2 along, and uninstalling aoe2 is refused while
    kingdom is active.
    """
    from botforge.core.services.mod_layout import default_repo_root, discover_mods
    from botforge.core.services.mod_registry import load_mod_definitions

    root = default_repo_root()
    assert root is not None and "kingdom" in discover_mods(root)
    definition = load_mod_definitions(root / "config", repo_root=root)["kingdom"]
    # the skeleton (#366): the mod BOOTS — enabled: true, the
    # declaration is the CURRENT drasah reality (singular rename per
    # #381), the grpc service + the cog load through the §7 loader
    assert definition.enabled is True
    assert definition.default_active is False
    assert definition.required_games == ("aoe2",)
