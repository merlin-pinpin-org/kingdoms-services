"""Season report: replay a season and print standings and match list (#205).

Replays the matches CSV chronologically through the repo's rating
systems (Elo — the legacy ladder's — and Glicko-2), then produces:

- standings tables: by rating, by wins, by winrate, by activity
  (matches played), by best streak, and a Glicko-2 rating table with
  rating deviation (confidence);
- a condensed match list: date, map, players, elo, result, delta.

Pure function over the dump + season YAML — no database needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kingdoms.mods.ladder.legacy_import import load_matches
from kingdoms.mods.ladder.models import LadderSettingsModel
from kingdoms.mods.ladder.service import RATING_SYSTEMS

DAY_MS = 86_400_000


@dataclass(slots=True)
class PlayerStanding:
    """One player's replayed record across every tracked metric."""

    user_id: str
    display_name: str
    rating: float = 0.0
    glicko_rating: float = 1500.0
    glicko_rd: float = 350.0
    wins: int = 0
    losses: int = 0
    streak: int = 0
    best_streak: int = 0
    rating_max: float = 0.0
    last_played_at: int = 0

    @property
    def matches(self) -> int:
        """Total completed matches of the player."""
        return self.wins + self.losses

    @property
    def winrate(self) -> float:
        """Win ratio in percent; 0.0 when no match."""
        return (self.wins / self.matches * 100.0) if self.matches else 0.0


@dataclass(slots=True)
class SeasonReport:
    """The replayed season: standings plus the condensed match list."""

    standings: dict[str, PlayerStanding] = field(default_factory=dict)
    matches: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class _ReplayState:
    """Mutable per-player state of one rating system's replay."""

    ratings: dict[str, float] = field(default_factory=dict)
    states: dict[str, dict[str, float]] = field(default_factory=dict)
    completed: dict[str, int] = field(default_factory=dict)


def _ms(timestamp: int) -> int:
    """Normalize a dump timestamp to milliseconds (dumps carry seconds)."""
    return timestamp * 1000 if 0 < timestamp < 10**12 else timestamp


def _display_name(match: Any, user_id: str) -> str:
    """Resolve a side's display name from the match row."""
    if user_id == match.host_discord_id:
        return match.host_name or user_id
    if user_id == match.guest_discord_id:
        return match.guest_name or user_id
    return user_id


def replay_season(
    matches_path: Path,
    settings: LadderSettingsModel | None = None,
) -> SeasonReport:
    """Replay the matches chronologically and build the season report.

    Elo replays the legacy ladder's own deltas (``elo_before`` /
    ``elo_diff``) so the standings match what the players knew;
    Glicko-2 replays the same results through the repo system for a
    second, deviation-aware view.
    """
    settings = settings or LadderSettingsModel()
    matches = sorted(load_matches(matches_path), key=lambda m: (m.completed_at, m.ladder_match_id))

    elo = _ReplayState()
    glicko = _ReplayState()
    glicko_system = RATING_SYSTEMS["glicko2"]
    standings: dict[str, PlayerStanding] = {}

    def _ensure(state: _ReplayState, user_id: str, initial: float, extra: dict[str, float]) -> float:
        if user_id not in state.ratings:
            state.ratings[user_id] = initial
            state.states[user_id] = dict(extra)
            state.completed[user_id] = 0
        return state.ratings[user_id]

    for match in matches:
        host, guest = match.host_discord_id, match.guest_discord_id
        won_host = match.winner_discord_id == host
        for user_id in (host, guest):
            if user_id not in standings:
                standings[user_id] = PlayerStanding(user_id=user_id, display_name=_display_name(match, user_id))
            standing = standings[user_id]
            standing.display_name = _display_name(match, user_id) or standing.display_name

        # Elo replay: legacy deltas verbatim.
        for user_id, before, delta, won in (
            (host, match.host_rating_before, match.host_delta, won_host),
            (guest, match.guest_rating_before, match.guest_delta, not won_host),
        ):
            if user_id not in elo.ratings:
                elo.ratings[user_id] = float(before)
                elo.completed[user_id] = 0
            elo.ratings[user_id] = float(before + delta)
            standing = standings[user_id]
            standing.rating = elo.ratings[user_id]
            standing.rating_max = max(standing.rating_max, standing.rating)
            standing.wins += 1 if won else 0
            standing.losses += 0 if won else 1
            if won:
                standing.streak = standing.streak + 1 if standing.streak >= 0 else 1
            else:
                standing.streak = standing.streak - 1 if standing.streak <= 0 else -1
            standing.best_streak = max(standing.best_streak, standing.streak, key=abs)
            standing.last_played_at = _ms(match.completed_at)

        # Glicko-2 replay of the same results.
        g_initial = glicko_system.initial_rating(settings)
        g_extra = glicko_system.initial_state(settings)
        h_g = _ensure(glicko, host, g_initial, g_extra)
        g_g = _ensure(glicko, guest, g_initial, g_extra)
        h_delta, _, h_state = glicko_system.apply(
            settings,
            h_g,
            glicko.states[host],
            g_g,
            glicko.states[guest],
            won_host,
            glicko.completed[host],
        )
        g_delta, _, g_state = glicko_system.apply(
            settings,
            g_g,
            glicko.states[guest],
            h_g,
            glicko.states[host],
            not won_host,
            glicko.completed[guest],
        )
        glicko.ratings[host] = h_g + h_delta
        glicko.ratings[guest] = g_g + g_delta
        glicko.states[host], glicko.states[guest] = h_state, g_state
        glicko.completed[host] += 1
        glicko.completed[guest] += 1
        standings[host].glicko_rating = glicko.ratings[host]
        standings[host].glicko_rd = glicko.states[host]["rd"]
        standings[guest].glicko_rating = glicko.ratings[guest]
        standings[guest].glicko_rd = glicko.states[guest]["rd"]

    condensed: list[dict[str, Any]] = []
    for match in matches:
        winner = match.winner_discord_id
        loser = match.guest_discord_id if winner == match.host_discord_id else match.host_discord_id
        condensed.append(
            {
                "ladder_match_id": match.ladder_match_id,
                "completed_at": _ms(match.completed_at),
                "map": match.map_name or "—",
                "host": match.host_name or match.host_discord_id,
                "guest": match.guest_name or match.guest_discord_id,
                "host_elo": match.host_rating_before + match.host_delta,
                "guest_elo": match.guest_rating_before + match.guest_delta,
                "winner": _display_name(match, winner),
                "loser": _display_name(match, loser),
                "delta": abs(match.host_delta),
                "host_civ": match.host_civ,
                "guest_civ": match.guest_civ,
                "duration_s": match.duration or None,
            }
        )
    return SeasonReport(standings=standings, matches=condensed)


def _fmt_duration(seconds: int | None) -> str:
    """Render a duration as h:mm:ss or mm:ss; dash when unknown."""
    if not seconds:
        return "-"
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _fmt_date(ms: int) -> str:
    """Render a ms timestamp as a short UTC date."""
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d") if ms else "?"


def format_report(report: SeasonReport, top: int | None = None) -> str:
    """Render the standings tables and match list as a Markdown report."""
    players = sorted(report.standings.values(), key=lambda p: (-p.rating, p.display_name))
    if top:
        players = players[:top]

    def _header(title: str, cols: list[str]) -> list[str]:
        pad = " | ".join(["#", *cols])
        sep = "|".join(["---"] * (len(cols) + 1))
        return [f"### {title}", "", f"{pad} |", f"{sep} |"]

    lines: list[str] = ["## Classements — replay chronologique", ""]

    rows = _header("Elo (replay des deltas legacy)", ["Joueur", "Elo", "V", "D", "Winrate", "Série", "Max"])
    for i, p in enumerate(players, 1):
        streak = f"+{p.streak}" if p.streak > 0 else str(p.streak or 0)
        rows.append(
            f"{i} | {p.display_name} | {p.rating:.0f} | {p.wins} | {p.losses} | "
            f"{p.winrate:.0f}% | {streak} | {p.rating_max:.0f}"
        )
    lines += [*rows, ""]

    by_wins = sorted(report.standings.values(), key=lambda p: (-p.wins, p.display_name))
    rows = _header("Par victoires", ["Joueur", "V", "D", "Winrate"])
    for i, p in enumerate(by_wins[: top or len(by_wins)], 1):
        rows.append(f"{i} | {p.display_name} | {p.wins} | {p.losses} | {p.winrate:.0f}%")
    lines += [*rows, ""]

    by_wr = sorted(
        (p for p in report.standings.values() if p.matches >= 3),
        key=lambda p: (-p.winrate, -p.matches, p.display_name),
    )
    rows = _header("Par winrate (≥3 matchs)", ["Joueur", "Winrate", "V", "D"])
    for i, p in enumerate(by_wr[: top or len(by_wr)], 1):
        rows.append(f"{i} | {p.display_name} | {p.winrate:.0f}% | {p.wins} | {p.losses}")
    lines += [*rows, ""]

    by_matches = sorted(report.standings.values(), key=lambda p: (-p.matches, p.display_name))
    rows = _header("Par activité (matchs joués)", ["Joueur", "Matchs", "V", "D"])
    for i, p in enumerate(by_matches[: top or len(by_matches)], 1):
        rows.append(f"{i} | {p.display_name} | {p.matches} | {p.wins} | {p.losses}")
    lines += [*rows, ""]

    by_streak = sorted(report.standings.values(), key=lambda p: (-p.best_streak, p.display_name))
    rows = _header("Meilleure série (consécutive, signée)", ["Joueur", "Série max"])
    for i, p in enumerate(by_streak[: top or len(by_streak)], 1):
        best = f"+{p.best_streak}" if p.best_streak > 0 else str(p.best_streak)
        rows.append(f"{i} | {p.display_name} | {best}")
    lines += [*rows, ""]

    glicko = sorted(report.standings.values(), key=lambda p: (-p.glicko_rating, p.display_name))
    rows = _header("Glicko-2 (replay même résultats)", ["Joueur", "Rating", "RD (±confiance)"])
    for i, p in enumerate(glicko[: top or len(glicko)], 1):
        rows.append(f"{i} | {p.display_name} | {p.glicko_rating:.0f} | ±{p.glicko_rd:.0f}")
    lines += [*rows, ""]

    lines += ["## Matchs — liste condensée", ""]
    lines.append("# | Date | Map | Hôte (civ) | Invité (civ) | Elo fin | Vainqueur | Δ | Durée")
    lines.append("--- | --- | --- | --- | --- | --- | --- | --- | ---")
    for m in sorted(report.matches, key=lambda x: (-x["completed_at"], x["ladder_match_id"])):
        host = f"{m['host']} ({m['host_civ']})" if m["host_civ"] else m["host"]
        guest = f"{m['guest']} ({m['guest_civ']})" if m["guest_civ"] else m["guest"]
        lines.append(
            f"{m['ladder_match_id']} | {_fmt_date(m['completed_at'])} | {m['map']} | "
            f"{host} | {guest} | {m['host_elo']}-{m['guest_elo']} | "
            f"**{m['winner']}** | {m['delta']} | {_fmt_duration(m['duration_s'])}"
        )
    return "\n".join(lines)
