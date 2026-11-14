"""Expose the modular mods' packages (botforge-services#178, §7).

Each §7 mod carries TWO packages: ``cogs/<mod>/`` (the Discord layer)
and ``grpc/<mod>/`` (its service). They import, at runtime, as
``botforge.mods.<name>.cogs`` and ``botforge.mods.<name>.grpc`` through
the mod entrypoint's file-based loading. For the test run this conftest
pre-imports both packages of every modular mod that has them, then the
pre-§7 single-package mods keep their sys.path alias — one module
object per name, never two (duplicates would mean duplicate exception
classes and identity bugs across imports).
"""

from __future__ import annotations

import importlib
import pkgutil
import sys
from pathlib import Path

_MODS_ROOT = Path(__file__).resolve().parent / "mods"


def _register_pre_section7(mod_dir: Path) -> None:
    """Import the old-layout package tree and alias it into botforge.mods."""
    name = mod_dir.name
    sys.path.insert(0, str(mod_dir / "src"))
    pkg = importlib.import_module(name)
    for module_info in pkgutil.walk_packages(pkg.__path__, prefix=f"{name}."):
        importlib.import_module(module_info.name)
    importlib.import_module("botforge.mods")
    for sub_name, module in list(sys.modules.items()):
        if sub_name == name or sub_name.startswith(f"{name}."):
            sys.modules[f"botforge.mods.{sub_name}"] = module


for _mod_dir in sorted(p for p in _MODS_ROOT.iterdir() if p.is_dir()):
    if (_mod_dir / "cogs" / _mod_dir.name / "__init__.py").is_file():
        from botforge.core.services.mod_entrypoint import load_mod_package

        load_mod_package(_mod_dir.name, repo_root=_MODS_ROOT.parents[1])
    elif (_mod_dir / "src" / _mod_dir.name / "__init__.py").is_file():
        _register_pre_section7(_mod_dir)
