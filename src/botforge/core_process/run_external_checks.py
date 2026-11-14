"""CLI entry: run every external-services battery, report all, exit on failures."""

from __future__ import annotations

import sys

from botforge.core_process.external_checks import render_report, run_all_batteries


def main() -> int:
    """Run the registered batteries and print the aggregated report."""
    import asyncio
    import logging
    import os

    import botforge.core_process.content_checks as _content_battery
    import botforge.core_process.mapsdata_checks as _mapsdata_battery
    import botforge.core_process.realtime_checks as _realtime_batteries

    del _content_battery, _mapsdata_battery, _realtime_batteries

    # A diagnostic CLI: DEBUG by default (the 429 request/response
    # diagnostics ride DEBUG), overridable through LOG_LEVEL like the
    # core server.
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "DEBUG").upper(),
        format="%(levelname)s %(name)s %(message)s",
    )

    results = asyncio.run(run_all_batteries())
    print(render_report(results))
    failed = [r for r in results if not r.ok]
    if failed:
        print(f"\n{len(failed)} external-services check(s) FAILED", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
