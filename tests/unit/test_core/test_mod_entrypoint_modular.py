"""Regression tests: §7 mods load as botforge.mods.<name>.cogs/.grpc.

The runtime import goes through load_mod_package's file-based loading
— the cog package lands under ``botforge.mods.<name>.cogs`` (the bot
loads it), the service under ``botforge.mods.<name>.grpc`` (the cog
calls it through the contract, via ``mod_service``). Without the
registration, the import fails and the mod stays silently inactive
(the exact bug that shipped a reference mod without its command).

The tests scaffold a throwaway mod with the committed tool (botforge
mod init) — they exercise the real machinery, not a fixture
handcrafted for the test.
"""

from __future__ import annotations

import sys
from pathlib import Path

from botforge.core.services.mod_entrypoint import load_mod_package, mod_service
from botforge.core.services.mod_init import init_mod

REPO_ROOT = Path(__file__).resolve().parents[3]


def _scaffolded(tmp_path: Path) -> str:
    """Scaffold a throwaway §7 modular mod named 'modloadtest'."""
    init_mod(tmp_path, "modloadtest", seasonal=False)
    return "modloadtest"


def test_mod_cog_and_service_load_separately(tmp_path: Path) -> None:
    """The cog imports as .cogs, the service as .grpc — one object each."""
    name = _scaffolded(tmp_path)
    loaded = load_mod_package(name, repo_root=tmp_path)
    assert loaded.name == name
    assert loaded.has_register
    assert loaded.has_service
    assert f"botforge.mods.{name}.cogs" in sys.modules
    assert f"botforge.mods.{name}.grpc" in sys.modules


def test_mod_service_accessor_returns_the_service_package(tmp_path: Path) -> None:
    """mod_service is the seam the cog uses to call its service."""
    name = _scaffolded(tmp_path)
    load_mod_package(name, repo_root=tmp_path)
    service = mod_service(name, repo_root=tmp_path)
    assert service is sys.modules[f"botforge.mods.{name}.grpc"]
    assert hasattr(service, "ModloadtestServiceImpl")


def test_cog_package_is_the_same_object(tmp_path: Path) -> None:
    """The loaded package and the registered cog module are one object."""
    name = _scaffolded(tmp_path)
    loaded = load_mod_package(name, repo_root=tmp_path)
    assert loaded.package is sys.modules[f"botforge.mods.{name}.cogs"]


def test_service_is_never_the_discord_layer(tmp_path: Path) -> None:
    """The service package imports no discord — the cog is the only layer."""
    name = _scaffolded(tmp_path)
    load_mod_package(name, repo_root=tmp_path)
    service = sys.modules[f"botforge.mods.{name}.grpc"]
    assert "discord" not in getattr(service, "__dict__", {})
