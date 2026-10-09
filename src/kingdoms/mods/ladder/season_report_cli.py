"""Season report CLI: replay a season dump and print standings + matches.

Usage: ``python -m kingdoms.mods.ladder.season_report_cli <matches.csv> [--top N]``
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> int:
    """Replay the season and print the report; exit 0 on success."""
    parser = argparse.ArgumentParser(description="Replay a season dump into standings")
    parser.add_argument("matches_csv", type=Path)
    parser.add_argument("--top", type=int, default=None)
    args = parser.parse_args()

    sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))
    from kingdoms.mods.ladder.season_report import format_report, replay_season

    print(format_report(replay_season(args.matches_csv), top=args.top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
