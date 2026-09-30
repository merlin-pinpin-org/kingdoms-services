"""Ladder matchmaking: reciprocal widening window + blossom maximum matching (kingdoms-services#134).

Pair compatibility is reciprocal (reference §5.1): |Δrating| must fit in
*both* players' windows, each widening with wait time up to the cap. The
pass then solves a **maximum matching** on the compatibility graph via
Edmonds' blossom algorithm, maximising the number of matches created —
no matchable player is left alone when a full matching exists.

The service is pure computation over snapshots: persistence, locks and
notification intents belong to the domain service.
"""

from __future__ import annotations

from dataclasses import dataclass

from kingdoms.mods.ladder.models import LadderSettingsModel


@dataclass(frozen=True, slots=True)
class QueueEntry:
    """One queued player as seen by a matchmaking pass."""

    user_id: str
    rating: float
    queued_at: int


@dataclass(frozen=True, slots=True)
class Pairing:
    """One decided match, ordered for determinism."""

    host_user_id: str
    guest_user_id: str


def threshold(entry: QueueEntry, settings: LadderSettingsModel, now: int) -> int:
    """Widened Elo threshold of one entry at ``now`` (capped)."""
    wait_s = max(0, (now - entry.queued_at) // 1000)
    steps = wait_s // settings.increment_interval if settings.increment_interval > 0 else 0
    return min(settings.elo_threshold_max, settings.base_elo_threshold + settings.elo_threshold_increment * steps)


def compatible(a: QueueEntry, b: QueueEntry, settings: LadderSettingsModel, now: int) -> bool:
    """Reciprocal check: |Δrating| fits in both players' windows."""
    delta = abs(a.rating - b.rating)
    return delta <= threshold(a, settings, now) and delta <= threshold(b, settings, now)


def _blossom_maximum_matching(n: int, adj: list[list[int]]) -> list[tuple[int, int]]:  # noqa: C901 - Edmonds'
    """Edmonds' blossom maximum matching (competitive-programming classic).

    Contraction form with explicit blossom bookkeeping; returns all
    matched pairs. O(V^3) worst case — fine for ladder queue sizes.
    """
    match = [-1] * n
    p = [-1] * n
    base = list(range(n))
    used = [False] * n
    blossom = [-1] * n

    def lca(a: int, b: int) -> int:
        """Lowest common ancestor on the alternating tree."""
        used_paths = [False] * n
        while True:
            a = base[a]
            used_paths[a] = True
            if match[a] == -1:
                break
            a = p[match[a]]
        while True:
            b = base[b]
            if used_paths[b]:
                return b
            b = p[match[b]]

    def mark_path(v: int, b: int, child: int) -> None:
        """Mark blossom vertices along the alternating path up to ``b``."""
        while base[v] != b:
            blossom[base[v]] = True
            blossom[base[match[v]]] = True
            p[v] = child
            child = match[v]
            v = p[match[v]]

    def contract(u: int, v: int, queue: list[int]) -> None:
        """Contract the blossom formed by edge (u, v) and re-enqueue."""
        cur = lca(u, v)
        for i in range(n):
            blossom[i] = False
        mark_path(u, cur, v)
        mark_path(v, cur, u)
        for i in range(n):
            if blossom[base[i]]:
                base[i] = cur
                if not used[i]:
                    used[i] = True
                    queue.append(i)

    def find_augmenting(root: int) -> bool:
        """BFS an alternating tree from an exposed root; augment when found."""
        for i in range(n):
            used[i] = False
            p[i] = -1
            base[i] = i
        used[root] = True
        queue = [root]
        while queue:
            v = queue.pop(0)
            for to in adj[v]:
                if base[v] == base[to] or match[v] == to:
                    continue
                if to == root or (match[to] != -1 and p[match[to]] != -1):
                    contract(v, to, queue)
                elif p[to] == -1:
                    p[to] = v
                    if match[to] == -1:
                        augment(to)
                        return True
                    used[match[to]] = True
                    queue.append(match[to])
        return False

    def augment(v: int) -> None:
        """Flip matched edges along the alternating path ending at v."""
        while v != -1:
            pv = p[v]
            nxt = match[pv]
            match[v] = pv
            match[pv] = v
            v = nxt

    res: list[tuple[int, int]] = []
    for v in range(n):
        if match[v] == -1:
            find_augmenting(v)
    for v in range(n):
        if v < match[v]:
            res.append((v, match[v]))
    return res


def matchmaking_pass(entries: list[QueueEntry], settings: LadderSettingsModel, now: int) -> list[Pairing]:
    """Run one pass: compatibility graph then blossom maximum matching."""
    n = len(entries)
    adj: list[list[int]] = [[] for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            if compatible(entries[i], entries[j], settings, now):
                adj[i].append(j)
                adj[j].append(i)
    pairs = _blossom_maximum_matching(n, adj)
    return [Pairing(entries[i].user_id, entries[j].user_id) for i, j in pairs]
