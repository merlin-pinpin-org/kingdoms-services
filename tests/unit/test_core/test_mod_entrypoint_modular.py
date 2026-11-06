"""Regression tests: modular mods load as kingdoms.mods.<name> (kingdoms#178).

The runtime import goes through load_mod_package's sys.path prepend +
namespace alias — without the alias, ``importlib.import_module`` of
``kingdoms.mods.<name>`` fails and the mod stays silently inactive
(the exact bug that shipped a reference mod without its command).

The tests scaffold a throwaway mod with the committed tool (kingdoms
mod init) — they exercise the real machinery, not a fixture handcrafted
for the test.
"""

from __future__ import annotations

import sys
from pathlib import Path

from kingdoms.core.services.mod_entrypoint import load_mod_package
from kingdoms.core.services.mod_init import init_mod

REPO_ROOT = Path(__file__).resolve().parents[3]


def _scaffolded(tmp_path: Path) -> str:
    """Scaffold a throwaway modular mod named 'modloadtest'."""
    init_mod(tmp_path, "modloadtest", seasonal=False)
    return "modloadtest"


def test_modular_mod_loads_through_the_namespace(tmp_path: Path) -> None:
    """The scaffolded package imports and its register hook is detected."""
    name = _scaffolded(tmp_path)
    sys.path.insert(0, str(tmp_path / "modules" / "mods" / name / "src"))
    try:
        loaded = load_mod_package(name, repo_root=tmp_path)
        assert loaded.name == name
        assert loaded.has_register
        assert f"kingdoms.mods.{name}" in sys.modules
    finally:
        sys.path.remove(str(tmp_path / "modules" / "mods" / name / "src"))


def test_registered_package_is_the_same_object(tmp_path: Path) -> None:
    """The namespace alias and the bare package are one module object."""
    name = _scaffolded(tmp_path)
    sys.path.insert(0, str(tmp_path / "modules" / "mods" / name / "src"))
    try:
        loaded = load_mod_package(name, repo_root=tmp_path)
        assert loaded.package is sys.modules[f"kingdoms.mods.{name}"]
        assert loaded.package is sys.modules.get(name)
    finally:
        sys.path.remove(str(tmp_path / "modules" / "mods" / name / "src"))
        for key in [f"kingdoms.mods.{name}", name]:
            sys.modules.pop(key, None)
