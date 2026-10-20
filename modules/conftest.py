"""Expose the modular mods' packages as ``kingdoms.mods.<name>`` (kingdoms#178).

Each mod's code lives in ``modules/mods/<mod>/src/`` and imports, at
runtime, as ``kingdoms.mods.<name>`` through the mod entrypoint's
sys.path prepend (the package directory IS the mod's ``src/`` root).
For the test run this conftest pre-imports every modular mod **and its
submodules** from the real path, then registers THE SAME module
objects under the ``kingdoms.mods.*`` names — one object per module,
never two (duplicates would mean duplicate exception classes and
identity bugs across imports).
"""

from __future__ import annotations

import importlib
import pkgutil
import sys
from pathlib import Path

_MODS_ROOT = Path(__file__).resolve().parent / "mods"


def _register(mod_dir: Path) -> None:
    """Import the mod's package tree once and alias it into kingdoms.mods."""
    name = mod_dir.name
    sys.path.insert(0, str(mod_dir / "src"))
    pkg = importlib.import_module(name)
    for module_info in pkgutil.walk_packages(pkg.__path__, prefix=f"{name}."):
        importlib.import_module(module_info.name)
    importlib.import_module("kingdoms.mods")
    for sub_name, module in list(sys.modules.items()):
        if sub_name == name or sub_name.startswith(f"{name}."):
            sys.modules[f"kingdoms.mods.{sub_name}"] = module


for _mod_dir in sorted(p for p in _MODS_ROOT.iterdir() if p.is_dir()):
    if (_mod_dir / "src" / _mod_dir.name / "__init__.py").is_file():
        _register(_mod_dir)
