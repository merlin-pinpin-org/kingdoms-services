"""External-services checks: one battery per provider, run together.

Every external provider (aoe2techtree, aoe2lobby, librematch, …) can
drift: an upstream API change, a renamed field, a broken mapping. Unit
tests mock those APIs; this module is the *real* battery — each check
hits the live upstream (or the deployed ext process) and verifies both
the API access and the provider mappings.

Design rules:

- every check runs even when a previous one failed (a battery must
  report ALL failures, never stop at the first);
- a check never raises: it returns :class:`CheckResult` and the runner
  aggregates;
- each provider registers its battery through
  :func:`register_battery`; the CLI (``python -m
  kingdoms.core_process.external_checks``) exits non-zero when any
  check failed, printing the full report.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("kingdoms.core.external_checks")


@dataclass(frozen=True)
class CheckResult:
    """One check's outcome (never an exception)."""

    provider: str
    name: str
    ok: bool
    detail: str = ""

    def render(self) -> str:
        """One report line for the CI log."""
        mark = "PASS" if self.ok else "FAIL"
        line = f"[{mark}] {self.provider} :: {self.name}"
        return f"{line} — {self.detail}" if self.detail else line


Check = Callable[[], Awaitable[CheckResult]]
Battery = Callable[[], Awaitable[list[CheckResult]]]


async def _run_check(provider: str, name: str, fn: Callable[[], Awaitable[None]]) -> CheckResult:
    """Run one check, converting any exception into a failed result."""
    try:
        await fn()
        return CheckResult(provider, name, ok=True)
    except Exception as exc:
        logger.debug("external check %s/%s failed", provider, name, exc_info=True)
        return CheckResult(provider, name, ok=False, detail=str(exc))


_BATTERIES: dict[str, Battery] = {}


def register_battery(provider: str, battery: Battery) -> None:
    """Register one provider's battery of real-API checks."""
    _BATTERIES[provider] = battery


def registered_batteries() -> tuple[str, ...]:
    """Return the provider keys that registered a battery."""
    return tuple(sorted(_BATTERIES))


async def run_all_batteries() -> list[CheckResult]:
    """Run every registered battery; ALL checks run, failures included."""
    results: list[CheckResult] = []
    for provider, battery in _BATTERIES.items():
        try:
            results.extend(await battery())
        except Exception as exc:
            results.append(CheckResult(provider, "battery", ok=False, detail=str(exc)))
    return results


def render_report(results: list[CheckResult]) -> str:
    """Render the aggregated report (CI log + PR summary)."""
    failed = [r for r in results if not r.ok]
    header = f"external-services checks: {len(results) - len(failed)}/{len(results)} passed"
    lines = [header, *["  " + r.render() for r in results]]
    return "\n".join(lines)


def _first_error(payload: Any) -> str:
    """Extract a short error message from an RPC error payload."""
    if isinstance(payload, BaseException):
        return str(payload)
    return str(payload)[:200]
