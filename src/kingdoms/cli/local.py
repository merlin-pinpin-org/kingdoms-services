"""Local stack wrappers: didactic front-ends to the Makefile docker targets.

Never duplicates the logic: every action shells out to `make` (which owns
docker compose) after explaining what it will do (kingdoms-services#191).
"""

from __future__ import annotations

import shutil
import subprocess


def _run(argv: list[str], *, confirm: str | None = None) -> int:
    """Run a fixed-argv command after explaining it; return exit code."""
    print(f"$ {' '.join(argv)}")
    if confirm is not None:
        answer = input(f"{confirm} Type 'yes' to continue: ").strip().lower()
        if answer != "yes":
            print("Aborted. Nothing was touched.")
            return 0
    result = subprocess.run(argv, check=False)
    return result.returncode


def require_make() -> int:
    """Fail with actionable advice when make is missing."""
    if shutil.which("make") is None:
        print("FAIL: 'make' is not installed.")
        print(" -> Linux/macOS: install build-essential (apt) or Xcode command line tools.")
        print(" -> Windows: use WSL, or run the docker compose commands from the Makefile by hand.")
        return 1
    return 0


def cmd_up() -> int:
    """Start the local stack (make dev-up)."""
    if require_make():
        return 1
    print("Starting the local stack: bot + MongoDB + Redis via docker compose.")
    print("This builds images and opens ports on your machine (nothing remote happens).\n")
    return _run(["make", "dev-up"])


def cmd_down() -> int:
    """Stop the local stack (make dev-down)."""
    if require_make():
        return 1
    print("Stopping the local stack. Your data volumes are kept.\n")
    return _run(["make", "dev-down"])


def cmd_logs() -> int:
    """Follow the bot logs (make dev-logs)."""
    if require_make():
        return 1
    print("Following the bot logs (Ctrl+C to stop watching; the bot keeps running).\n")
    return _run(["make", "dev-logs"])


def cmd_status() -> int:
    """Show local containers state (docker compose ps)."""
    if require_make():
        return 1
    print("Showing the state of your local containers.\n")
    return _run(["docker", "compose", "ps"])


def cmd_reset() -> int:
    """Stop the stack and delete local data volumes (make clean)."""
    if require_make():
        return 1
    print("reset = down + delete the data volumes (MongoDB and Redis content).")
    return _run(
        ["make", "clean"],
        confirm="This DESTROYS local bot data (databases). It cannot be undone.",
    )


def cmd_test() -> int:
    """Run the test suite (make test)."""
    if require_make():
        return 1
    print("Running the test suite — no Discord token, no network, no Docker needed.\n")
    return _run(["make", "test"])


def cmd_lint() -> int:
    """Run ruff then mypy (make lint, make typecheck)."""
    if require_make():
        return 1
    print("Running ruff and mypy on the codebase.\n")
    rc = _run(["make", "lint"])
    if rc == 0:
        rc = _run(["make", "typecheck"])
    return rc


def main(argv: list[str]) -> int:
    """Dispatch `kingdoms local <action>`; return exit code."""
    actions = {
        "up": cmd_up,
        "down": cmd_down,
        "logs": cmd_logs,
        "status": cmd_status,
        "reset": cmd_reset,
        "test": cmd_test,
        "lint": cmd_lint,
    }
    if len(argv) != 1 or argv[0] not in actions:
        print("Usage: kingdoms local {up|down|logs|status|reset|test|lint}")
        print("  up     — start bot + MongoDB + Redis (docker compose)")
        print("  down   — stop the stack, keep your data")
        print("  logs   — follow the bot logs")
        print("  status — show the containers' state")
        print("  reset  — stop AND delete local data (asks confirmation)")
        print("  test   — run the test suite (safe: no token, no network)")
        print("  lint   — run ruff + mypy")
        return 2 if argv else 0
    return actions[argv[0]]()
