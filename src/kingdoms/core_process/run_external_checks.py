"""CLI entry: run every external-services battery, report all, exit on failures."""

from __future__ import annotations

import sys

from kingdoms.core_process.external_checks import render_report, run_all_batteries


def main() -> int:
    """Run the registered batteries and print the aggregated report."""
    import asyncio

    import kingdoms.core_process.content_checks as _content_battery
    import kingdoms.core_process.realtime_checks as _realtime_batteries

    del _content_battery, _realtime_batteries

    results = asyncio.run(run_all_batteries())
    print(render_report(results))
    failed = [r for r in results if not r.ok]
    if failed:
        print(f"\n{len(failed)} external-services check(s) FAILED", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
