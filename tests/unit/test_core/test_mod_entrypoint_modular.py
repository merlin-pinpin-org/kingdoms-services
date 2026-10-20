"""Regression tests: modular mods load as kingdoms.mods.<name> (kingdoms#178).

The runtime import goes through load_mod_package's sys.path prepend +
namespace alias — without the alias, ``importlib.import_module`` of
``kingdoms.mods.<name>`` fails and the mod stays silently inactive
(the exact bug that shipped the clans mod without its /clan command).
"""

from __future__ import annotations

import sys
from pathlib import Path

from kingdoms.core.services.mod_entrypoint import load_mod_package

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_modular_mod_loads_through_the_namespace() -> None:
    """The clans package imports and its register hook is detected."""
    loaded = load_mod_package("clans", repo_root=REPO_ROOT)
    assert loaded.name == "clans"
    assert loaded.has_register
    assert "kingdoms.mods.clans" in sys.modules


def test_registered_package_is_the_same_object() -> None:
    """The namespace alias and the bare package are one module object."""
    loaded = load_mod_package("clans", repo_root=REPO_ROOT)
    assert loaded.package is sys.modules["kingdoms.mods.clans"]
    assert loaded.package is sys.modules.get("clans")
