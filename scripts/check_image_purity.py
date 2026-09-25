#!/usr/bin/env python3
"""Runtime-image purity guard (kingdoms-services#106).

Asserts that the test tooling is absent from the runtime image: every
forbidden import must fail with ModuleNotFoundError inside the built
image. A production code path must never import test dependencies
(SimCord, pytest, mocks) — the runtime image runs on the environment
VPSes and must never carry test code.

Two layers, both fail-closed:

- source check: ``src/`` must not reference test tooling;
- image check: ``import <module>`` inside the built image must fail.

Usage (CI / make check):

    ./scripts/check_image_purity.py --image kingdoms-bot:pr
    ./scripts/check_image_purity.py --source-only
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from shutil import which

REPO_ROOT = Path(__file__).resolve().parent.parent

# Test tooling that must never appear in production code or the runtime
# image. The image check lists installed packages only: unittest.mock is
# standard-library and would be importable by design, so the source check
# alone forbids mock usage in src/.
FORBIDDEN_MODULES = ("simcord", "pytest")
SOURCE_PATTERN = re.compile(r"simcord|pytest|unittest\.mock|MagicMock", re.IGNORECASE)


def check_source() -> int:
    """Fail when a file under src/ references test tooling."""
    failures: list[str] = []
    for path in sorted((REPO_ROOT / "src").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if SOURCE_PATTERN.search(line):
                failures.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")
    if failures:
        print("::error::src/ must never reference test tooling (simcord, pytest, mocks):")
        for failure in failures:
            print(f"::error::{failure}")
        return 1
    print("source purity OK: no test tooling referenced in src/")
    return 0


def check_image(image: str) -> int:
    """Fail unless every forbidden import raises ModuleNotFoundError in the image."""
    failures: list[str] = []
    for module in FORBIDDEN_MODULES:
        result = subprocess.run(
            [str(which("docker")), "run", "--rm", "--entrypoint", "python", image, "-c", f"import {module}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            failures.append(module)
            continue
        if "ModuleNotFoundError" not in result.stderr:
            print(
                f"::error::import {module} failed for an unexpected reason "
                f"(expected ModuleNotFoundError) — inspect the image:"
            )
            print(result.stderr)
            return 1
    if failures:
        print(
            "::error::test tooling is importable in the runtime image "
            f"(found: {', '.join(failures)}) — the image must be built --no-dev"
        )
        return 1
    print(f"image purity OK: none of {', '.join(FORBIDDEN_MODULES)} is importable in {image}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", help="image to check (omitted: source check only)")
    parser.add_argument("--source-only", action="store_true", help="skip the image check")
    args = parser.parse_args()

    if check_source() != 0:
        return 1
    if args.source_only or not args.image:
        return 0
    return check_image(args.image)


if __name__ == "__main__":
    sys.exit(main())
