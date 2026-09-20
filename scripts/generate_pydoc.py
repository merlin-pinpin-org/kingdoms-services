#!/usr/bin/env python3
"""Generate pydoc HTML documentation for all Kingdoms modules.

Scans the Python source tree, renders HTML documentation for every module
with pydoc, mirrors the source layout under docs/DEVELOPMENT/pydoc/, and
writes an index.html linking every generated page.
"""

from __future__ import annotations

import argparse
import html
import importlib
import pydoc
import re
import sys
from pathlib import Path


def add_source_root(src_root: Path) -> None:
    """Add src_root to sys.path and import the package it contains."""
    sys.path.insert(0, str(src_root))


def find_modules(src_root: Path) -> list[str]:
    """Return importable module names for every .py file under src_root."""
    package = src_root.name
    modules: list[str] = []
    for path in sorted(src_root.rglob("*.py")):
        rel = path.relative_to(src_root)
        parts = list(rel.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
            if not parts:
                continue
        modules.append(".".join([package, *parts]))
    return modules


def import_all(modules: list[str]) -> list[tuple[str, object]]:
    """Import every module, skipping ones that fail to import."""
    imported: list[tuple[str, object]] = []
    for name in modules:
        try:
            imported.append((name, importlib.import_module(name)))
        except Exception as exc:
            print(f"warning: could not import {name}: {exc}", file=sys.stderr)
    return imported


def write_pages(
    imported: list[tuple[str, object]],
    output_dir: Path,
    src_root: Path,
) -> list[tuple[str, Path]]:
    """Render each module to docs/DEVELOPMENT/pydoc/<mirrored-path>.html."""
    written: list[tuple[str, Path]] = []
    for name, module in imported:
        content = pydoc.html.page(name, pydoc.html.document(module, name))
        content = re.sub(
            r"('[A-Za-z_.]+':\s*'[A-Za-z_.]+:)\d{7,}'",
            r"\g<1>MEMORY_ADDRESS'",
            content,
        )
        content = re.sub(
            r"0x[0-9a-f]+|\b\d{12,}\b",
            "0xMEMORY_ADDRESS",
            content,
        )
        src_prefix = re.escape(str(src_root.resolve()))
        content = re.sub(
            rf"{src_prefix}",
            "SOURCE_ROOT",
            content,
        )
        rel_dir = Path(*name.split(".")[:-1])
        out_dir = output_dir / rel_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{name.split('.')[-1]}.html"
        out_path.write_text(content, encoding="utf-8")
        written.append((name, out_path))
        print(f"generated {out_path}")
    return written


def write_index(
    written: list[tuple[str, Path]],
    output_dir: Path,
    package: str,
    src_root: Path,
) -> None:
    """Write index.html linking every generated page."""
    del package, src_root  # unused, kept for interface stability
    items = "\n".join(
        f'      <li><a href="{html.escape(path.relative_to(output_dir).as_posix())}">'
        f"{html.escape(name)}</a></li>"
        for name, path in written
    )
    content = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Kingdoms pydoc index</title>
<style>
body {{ font-family: sans-serif; margin: 2rem; }}
h1 {{ font-size: 1.4rem; }}
ul {{ line-height: 1.6; }}
</style>
</head>
<body>
<h1>Kingdoms pydoc index</h1>
<p>Auto-generated from the Python source tree by
<code>scripts/generate_pydoc.py</code>.</p>
<ul>
{items}
</ul>
</body>
</html>
"""
    (output_dir / "index.html").write_text(content, encoding="utf-8")
    print(f"generated {output_dir / 'index.html'}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("../kingdoms-services/src/kingdoms").resolve()
        if Path("../kingdoms-services/src/kingdoms").exists()
        else Path("kingdoms-services/src/kingdoms"),
        help="Path to the Python package to document (default: kingdoms-services/src/kingdoms)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/DEVELOPMENT/pydoc"),
        help="Output directory for HTML files (default: docs/DEVELOPMENT/pydoc)",
    )
    args = parser.parse_args()

    src_root = args.source.resolve()
    if not src_root.is_dir():
        print(
            f"warning: source directory not found: {src_root} "
            "kingdoms-services is not initialized yet; nothing to generate.",
            file=sys.stderr,
        )
        return 0

    output_dir = args.output.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    add_source_root(src_root.parent)
    modules = find_modules(src_root)
    if not modules:
        print(f"warning: no Python modules found under {src_root}", file=sys.stderr)
        return 0

    imported = import_all(modules)
    written = write_pages(imported, output_dir, src_root)
    write_index(written, output_dir, src_root.name, src_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
