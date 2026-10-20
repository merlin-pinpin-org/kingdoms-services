"""`kingdoms mod` — mod scaffolding commands (init).

CLI-first mandate (AGENTS.md): recurring mod operations live here, not
in improvised shell commands — sessions and designers run
``uv run kingdoms mod init <name>`` and get a structured, CI-green
starting point (guide: docs/GUIDES/mod-init.md in the kingdoms repo).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from kingdoms.core.services.mod_init import init_mod
from kingdoms.core.services.mod_layout import discover_mods

GUIDE_URL = (
    "https://github.com/merlin-pinpin-org/kingdoms/blob/main/docs/GUIDES/mod-init.md"
)


def cmd_list(repo_root: str) -> int:
    """Print every declared mod with its layout (legacy or modular)."""
    layouts = discover_mods(Path(repo_root))
    if not layouts:
        print("No mod declared (no config/mods/*.yaml, no modules/mods/*/mod.yaml).")
        return 0
    print(f"{'MOD':<20} LAYOUT")
    for name, layout in sorted(layouts.items()):
        kind = "legacy (config/mods)" if not layout.modular else "modular (modules/mods)"
        print(f"{name:<20} {kind}")
    return 0


def cmd_init(repo_root: str, name: str, seasonal: bool | None) -> int:
    """Generate a new self-contained mod directory."""
    if seasonal is None:
        answer = input("Seasonal mod? (y=seasonal, n=permanent): ").strip().lower()
        seasonal = answer.startswith("y")
    try:
        result = init_mod(Path(repo_root), name, seasonal=seasonal)
    except (ValueError, FileExistsError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Mod '{name}' scaffolded under {result.mod_dir}:")
    for label, path in (
        ("declaration", result.declaration),
        ("package", result.package),
        ("locales", ", ".join(str(p) for p in result.locales)),
        ("test", result.test),
        ("readme", result.readme),
    ):
        print(f"  {label:<12} {path}")
    print()
    print("Next steps (see the guide: " + GUIDE_URL + "):")
    print("  1. Fill mod.yaml (channels/roles/workflows) — schema validated at startup")
    print(f"  2. Implement the mod logic in src/{name}/ against the core seams")
    print("  3. Add locale entries in locales/en.yaml + fr.yaml")
    print(f"  4. Document the mod in the kingdoms repo: docs/MODS/{name}/ (TEMPLATE-based)")
    print("  5. Run: make lint typecheck test")
    return 0


def main(argv: list[str]) -> int:
    """Dispatch `kingdoms mod <action>`; return exit code."""
    parser = argparse.ArgumentParser(
        prog="kingdoms mod",
        description="Mod scaffolding: init a new mod, list declared mods.",
    )
    sub = parser.add_subparsers(dest="action")
    init_parser = sub.add_parser("init", help="generate a new mod scaffold")
    init_parser.add_argument("name", help="mod id (simple slug, e.g. 'my-mod')")
    init_group = init_parser.add_mutually_exclusive_group()
    init_group.add_argument("--seasonal", action="store_true", help="seasonal mod (lifecycle panel + season salons)")
    init_group.add_argument("--permanent", action="store_true", help="permanent mod (guild-level config panel)")
    list_parser = sub.add_parser("list", help="list declared mods and their layout")
    list_parser.add_argument("--repo-root", default=None)
    args = parser.parse_args(argv)
    if args.action == "init":
        seasonal = True if args.seasonal else (False if args.permanent else None)
        return cmd_init(str(Path.cwd()), args.name, seasonal)
    if args.action == "list":
        repo_root = args.repo_root or str(Path.cwd())
        return cmd_list(repo_root)
    parser.print_help()
    return 0
