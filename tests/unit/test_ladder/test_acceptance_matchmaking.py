"""Acceptance-property tests: matchmaking thresholds and matching maximality (kingdoms-services#139).

Encodes reference §9.3 and §9.4 as permanent properties, plus a
brute-force cross-check of the blossom maximum matching on small
random graphs.
"""

from __future__ import annotations

import itertools
import random

import pytest

from kingdoms.mods.ladder.matchmaking import QueueEntry, compatible, matchmaking_pass
from kingdoms.mods.ladder.models import LadderSettingsModel

NOW = 1_000_000


def _settings(**overrides: int) -> LadderSettingsModel:
    return LadderSettingsModel(**overrides)


def _brute_force_max_matching(n: int, adj: list[list[int]]) -> int:
    """Exact maximum matching by enumeration (tiny graphs only)."""
    edges = [(i, j) for i in range(n) for j in adj[i] if i < j]
    best = 0
    for size in range(len(edges), 0, -1):
        for combo in itertools.combinations(edges, size):
            vertices = [v for edge in combo for v in edge]
            if len(vertices) == len(set(vertices)):
                return size
    return best


def test_blossom_matches_brute_force_on_random_graphs() -> None:
    """Blossom output size == brute-force maximum on random small graphs."""
    rng = random.Random(2024)  # noqa: S311 - test rng
    for graph_index in range(40):
        n = rng.randint(2, 8)
        edges: set[tuple[int, int]] = set()
        for i in range(n):
            for j in range(i + 1, n):
                if rng.random() < 0.4:
                    edges.add((i, j))
        adj: list[list[int]] = [[] for _ in range(n)]
        for i, j in edges:
            adj[i].append(j)
            adj[j].append(i)
        from kingdoms.mods.ladder.matchmaking import _blossom_maximum_matching

        pairs = _blossom_maximum_matching(n, adj)
        assert set(pairs) <= edges, f"phantom edge in graph {graph_index}: {pairs} vs {sorted(edges)}"
        assert len(pairs) == _brute_force_max_matching(n, adj), f"graph {graph_index}: {sorted(edges)}"


def test_property_pair_within_threshold_matches_in_one_tick() -> None:
    """§9.3: Δrating ≤ base → matched in a single pass."""
    settings = _settings()
    entries = [QueueEntry("a", 1000, NOW), QueueEntry("b", 1040, NOW)]
    pairs = matchmaking_pass(entries, settings, NOW)
    assert len(pairs) == 1


def test_property_never_paired_beyond_cap() -> None:
    """§9.3: beyond the cap, never paired, however long the wait."""
    settings = _settings(elo_threshold_max=200)
    entries = [QueueEntry("a", 1000, NOW - 10**9), QueueEntry("b", 1500, NOW - 10**9)]
    assert matchmaking_pass(entries, settings, NOW) == []


def test_property_no_pairable_player_left_alone() -> None:
    """§9.4: with a full matching available, everyone gets paired."""
    settings = _settings()
    entries = [QueueEntry(str(i), 1000 + i, NOW - 3600_000) for i in range(4)]
    pairs = matchmaking_pass(entries, settings, NOW)
    matched = {p.host_user_id for p in pairs} | {p.guest_user_id for p in pairs}
    assert matched == {"0", "1", "2", "3"}


def test_property_two_players_always_paired_when_compatible() -> None:
    """§9.4 corner: two compatible players are always paired."""
    settings = _settings()
    for delta in (0, 30, 59):
        entries = [QueueEntry("a", 1000, NOW - 60_000), QueueEntry("b", 1000 + delta, NOW - 60_000)]
        assert len(matchmaking_pass(entries, settings, NOW)) == 1


def test_property_reciprocity_required() -> None:
    """A pair needs BOTH windows to fit — one wide window is not enough."""
    settings = _settings(base_elo_threshold=60, increment_interval=15)
    fresh = QueueEntry("a", 1000, NOW)
    waited = QueueEntry("b", 1150, NOW - 90_000)
    assert not compatible(fresh, waited, settings, NOW)
    assert matchmaking_pass([fresh, waited], settings, NOW) == []


@pytest.mark.parametrize("wait_s,expected", [(0, 60), (14, 60), (15, 80), (45, 120), (10000, 400)])
def test_threshold_widening_schedule(wait_s: int, expected: int) -> None:
    """Threshold = base + increment x steps, capped at max."""
    settings = _settings()
    entry = QueueEntry("a", 1000, NOW - wait_s * 1000)
    from kingdoms.mods.ladder.matchmaking import threshold

    assert threshold(entry, settings, NOW) == expected, f"wait={wait_s}s"
