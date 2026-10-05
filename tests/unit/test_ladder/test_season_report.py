"""Unit tests for the season report replay (kingdoms-services#205).

Chronological replay through the repo rating systems, standings
metrics (wins, winrate, streaks, activity), and the condensed match
list. Elo replays the legacy deltas verbatim.
"""

from __future__ import annotations

from pathlib import Path

from kingdoms.mods.ladder.season_report import format_report, replay_season

_MATCH_HEADER = (
    "ladder_match_id,status,match_id,map_name,game_completed_at,"
    "host_name,host_discord_id,host_profile_id,host_elo_before,host_elo_diff,"
    "guest_name,guest_discord_id,guest_profile_id,guest_elo_before,"
    "guest_elo_diff,winner_discord_id"
)

MATCHES_CSV = "\n".join(
    [
        _MATCH_HEADER,
        "2,COMPLETED,9002,Kawasan,200,Alpha,111,101,1020,-10,Bravo,222,201,1000,10,222",
        "1,COMPLETED,9001,Arabia,100,Alpha,111,101,1000,20,Bravo,222,201,1000,-20,111",
        "3,CANCELED,,Hideout,,,,,Alpha,111,101,,,Bravo,222,201,,,,,,,,",
        "",
    ]
)


def _matches_csv(tmp_path: Path) -> Path:
    path = tmp_path / "matches.csv"
    path.write_text(MATCHES_CSV, encoding="utf-8")
    return path


def test_replay_elo_uses_legacy_deltas(tmp_path: Path) -> None:
    report = replay_season(_matches_csv(tmp_path))
    alpha = report.standings["111"]
    bravo = report.standings["222"]
    assert alpha.rating == 1010
    assert bravo.rating == 1010
    assert alpha.wins == 1 and alpha.losses == 1
    assert bravo.wins == 1 and bravo.losses == 1
    assert alpha.rating_max == 1020
    assert alpha.best_streak == 1 and alpha.streak == -1


def test_replay_canceled_matches_are_skipped(tmp_path: Path) -> None:
    report = replay_season(_matches_csv(tmp_path))
    assert len(report.matches) == 2
    assert all(m["ladder_match_id"] in {"1", "2"} for m in report.matches)


def test_replay_glicko_moves_ratings(tmp_path: Path) -> None:
    report = replay_season(_matches_csv(tmp_path))
    alpha = report.standings["111"]
    assert alpha.glicko_rating != 1500.0
    assert alpha.glicko_rd < 350.0


def test_format_report_renders_tables(tmp_path: Path) -> None:
    report = replay_season(_matches_csv(tmp_path))
    text = format_report(report)
    assert "## Classements" in text
    assert "## Matchs" in text
    assert "Alpha" in text and "Kawasan" in text
