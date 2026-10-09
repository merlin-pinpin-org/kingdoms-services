"""Unit tests for the synthetic test-data generator (scripts/generate_test_data.py)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests.unit.test_ladder.test_season_import import FakeDatabase

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "generate_test_data.py"


def _generate(tmp_path: Path, players: int = 12, matches: int = 40) -> Path:
    out = tmp_path / "testdata"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(out),
            "--players",
            str(players),
            "--matches",
            str(matches),
            "--seed",
            "42",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "test data:" in result.stdout
    return out


def test_generator_is_deterministic(tmp_path: Path) -> None:
    first = _generate(tmp_path / "a")
    second = _generate(tmp_path / "b")
    for name in ("test-season.yaml", "test-users.csv", "test-matches.csv"):
        assert (first / name).read_text(encoding="utf-8") == (second / name).read_text(encoding="utf-8")


def test_generated_match_ids_match_users(tmp_path: Path) -> None:
    import csv

    out = _generate(tmp_path)
    users = {r["discord_id"]: r["profile_id"] for r in csv.DictReader((out / "test-users.csv").open())}
    rows = list(csv.DictReader((out / "test-matches.csv").open()))
    ids = {r["host_discord_id"] for r in rows} | {r["guest_discord_id"] for r in rows}
    assert ids <= set(users)
    profs = {r["host_profile_id"] for r in rows} | {r["guest_profile_id"] for r in rows}
    assert profs <= set(users.values())
    winners = {r["winner_discord_id"] for r in rows}
    assert winners <= ids


async def test_generated_dataset_imports_end_to_end(tmp_path: Path) -> None:
    from kingdoms.mods.ladder.season_import import import_season

    out = _generate(tmp_path)
    report = await import_season(
        FakeDatabase(),
        out / "test-season.yaml",
        out / "test-users.csv",
        out / "test-matches.csv",
        "123456789",
    )
    assert report.seed["maps"] == 18
    assert report.seed["map_pools"] == 3
    assert report.players == 12
    assert report.matches == 40
    assert report.rotations >= 2
