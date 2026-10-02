"""Doctor command: checks the local environment and prints actionable advice.

Never raises raw stacktraces at the user; every failed check comes with
"what do I do now?" guidance (kingdoms-services#190).
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass

OK = "OK "
WARN = "WARN"
FAIL = "FAIL"
HEALTH_PORT = 8000
HEALTH_HOST = "127.0.0.1"
HEALTH_TIMEOUT = 0.5


@dataclass(frozen=True)
class Check:
    """Result of a single doctor check."""

    name: str
    status: str
    detail: str
    fix: str | None = None


def _check_python() -> Check:
    version = sys.version_info[:2]
    detail = f"Python {sys.version.split()[0]}"
    if version >= (3, 12):
        return Check("python", OK, detail)
    return Check(
        "python",
        FAIL,
        f"{detail} found, 3.12+ required",
        "Install Python 3.12+ (https://www.python.org/downloads/), then rerun: uv sync",
    )


def _check_uv() -> Check:
    uv = shutil.which("uv")
    if uv:
        return Check("uv", OK, f"found at {uv}")
    return Check(
        "uv",
        FAIL,
        "uv not found",
        "Install uv: curl -LsSf https://astral.sh/uv/install.sh | sh  (or: pip install uv)",
    )


def _check_docker() -> Check:
    docker = shutil.which("docker")
    if not docker:
        return Check(
            "docker",
            WARN,
            "docker not found",
            "Needed only for the local stack (make dev-up). Install Docker Desktop or Podman.",
        )
    return Check("docker", OK, f"found at {docker}")


def _check_compose() -> Check:
    if shutil.which("docker") is None:
        return Check("docker compose", WARN, "skipped (no docker)", None)
    found = shutil.which("docker-compose") is not None
    try:
        probe = subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True,
            timeout=10,
            check=False,
        )
        found = found or probe.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        pass
    if found:
        return Check("docker compose", OK, "available")
    return Check(
        "docker compose",
        FAIL,
        "not available",
        "Update Docker Desktop, or install the compose plugin: docker docs > compose > install",
    )


def _check_env(root: str) -> Check:
    env = os.path.join(root, ".env")
    if not os.path.isfile(env):
        example = os.path.join(root, ".env.example")
        hint = f"cp {example} {env}" if os.path.isfile(example) else f"create {env}"
        return Check(".env", FAIL, "missing", f"Run: {hint}  then fill in DISCORD_TOKEN (never commit it)")
    return Check(".env", OK, f"found at {env}")


def _check_port() -> Check:
    if shutil.which("docker") is None:
        return Check("bot (port 8000)", WARN, "skipped (no docker)", None)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(HEALTH_TIMEOUT)
            if s.connect_ex((HEALTH_HOST, HEALTH_PORT)) == 0:
                return Check("bot (port 8000)", OK, "something is listening — try http://localhost:8000/healthz")
            return Check(
                "bot (port 8000)",
                WARN,
                "nothing listening",
                "Start the local stack: make dev-up (then make dev-logs)",
            )
    except OSError:
        return Check("bot (port 8000)", WARN, "could not probe", None)


def run_checks(root: str) -> list[Check]:
    """Run all doctor checks and return their results."""
    checks: list[Callable[[], Check]] = [
        _check_python,
        _check_uv,
        _check_docker,
        _check_compose,
        lambda: _check_env(root),
        _check_port,
    ]
    return [c() for c in checks]


def print_report(checks: list[Check]) -> int:
    """Print the report; return the process exit code."""
    print("kingdoms doctor — checking your local environment\n")
    for c in checks:
        print(f"[{c.status}] {c.name}: {c.detail}")
        if c.fix:
            print(f"        -> {c.fix}")
    print()
    if any(c.status == FAIL for c in checks):
        print("Some checks failed. Fix them (see the -> lines above) and rerun: uv run kingdoms doctor")
        return 1
    if any(c.status == WARN for c in checks):
        print("Everything essential is fine; some optional tools are missing (WARN lines).")
        return 0
    print("All good! Next steps: make test (safe, no network), then make dev-up.")
    return 0
