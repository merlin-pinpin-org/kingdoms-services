"""ext-aoe2lobby live adapter: WebSocket client (aoe2lobby.com).

Real protocol (validated on a live integration): the server pushes global
state deltas over ``wss://data.aoe2lobby.com/ws/`` — subscription is
global (no per-profile filter), so this adapter is the client-side
reducer: it rebuilds the lobby/spectate state from ``*_match_all``
snapshots and ``*_match_update``/``*_match_remove`` deltas, derives the
per-profile status transitions, and yields them as normalized events in
the wire ``MatchEvent`` shape.

Known traps covered here (aoe2lobby reference §4/§7): a ~15 s gap
separates ``lobby_match_remove`` from the matching ``spectate_match_*``
when a game launches — a lobby removal is never downgraded before the
grace period elapses; ``*_player_remove`` lists profile ids while
``*_match_remove`` lists match ids; profile ids compare as ``str``;
frames can exceed 1 MB (max_size raised); ``*_all`` payloads can be
``null``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import websockets

from kingdoms.core.games.aoe2.grace import GRACE_PERIOD_S

logger = logging.getLogger(__name__)

DEFAULT_WS_URL = "wss://data.aoe2lobby.com/ws/"
DEFAULT_MAX_SIZE = 10 * 1024 * 1024
DEFAULT_RECONNECT_DELAY_S = 5.0
DEFAULT_HEARTBEAT_S = 90.0
DEFAULT_REFRESH_S = 60.0

CONTEXT_LOBBY = "lobby"
CONTEXT_SPECTATE = "spectate"
_CONTEXTS = (CONTEXT_LOBBY, CONTEXT_SPECTATE)

STATUS_LOBBY = "lobby"
STATUS_GAME = "game"


class Aoe2LobbyAdapter:
    """WebSocket client reducing aoe2lobby deltas to player transitions."""

    def __init__(
        self,
        ws_url: str = DEFAULT_WS_URL,
        reconnect_delay_s: float = DEFAULT_RECONNECT_DELAY_S,
        grace_s: float = GRACE_PERIOD_S,
        heartbeat_s: float = DEFAULT_HEARTBEAT_S,
        max_size: int = DEFAULT_MAX_SIZE,
        refresh_s: float = DEFAULT_REFRESH_S,
    ) -> None:
        self._ws_url = ws_url
        self._reconnect_delay_s = reconnect_delay_s
        self._grace_s = grace_s
        self._heartbeat_s = heartbeat_s
        self._max_size = max_size
        self._refresh_s = refresh_s
        self._matches: dict[str, dict[str, dict[str, Any]]] = {c: {} for c in _CONTEXTS}
        self._player_status: dict[str, tuple[str, str]] = {}
        self._pending_closes: dict[str, tuple[str, float]] = {}
        self._last_snapshot_at = 0

    async def stream_events(self) -> AsyncIterator[dict[str, Any]]:
        """Yield normalized event dicts from the WebSocket, forever.

        Reconnects with a fixed backoff on transport errors or heartbeat
        timeouts and yields nothing while disconnected — the stream
        stays alive, the consumer sees silence, not failures. After a
        reconnect the server pushes a fresh ``match_all`` snapshot and
        the reducer rebuilds its state from it: the state is kept
        across reconnects so the diff between snapshots yields the
        missing transitions (deltas may be sparse server-side).
        """
        while True:
            try:
                async with websockets.connect(self._ws_url, max_size=self._max_size) as ws:
                    await self._subscribe(ws)
                    logger.info("aoe2lobby ws connected: %s", self._ws_url)
                    while True:
                        raw = await asyncio.wait_for(ws.recv(), timeout=self._heartbeat_s)
                        for event in self.apply_frame(raw):
                            yield event
                        for event in self._sweep_pending_closes():
                            yield event
                        if self._last_snapshot_at and _now_ms() - self._last_snapshot_at > self._refresh_s * 1000:
                            logger.info("aoe2lobby ws refreshing: reconnect for a fresh snapshot")
                            break
            except (TimeoutError, websockets.WebSocketException, OSError):
                logger.warning("aoe2lobby ws disconnected; retrying in %ss", self._reconnect_delay_s)
            except Exception:
                logger.exception("aoe2lobby ws unexpected error; retrying in %ss", self._reconnect_delay_s)
            await asyncio.sleep(self._reconnect_delay_s)

    async def _subscribe(self, ws: Any) -> None:
        """Subscribe to both documented contexts (lobby + spectate).

        The subscription is global — every observable lobby and running
        game — because the service offers no per-profile filter; the
        reducer does the profile filtering client-side.
        """
        for context in _CONTEXTS:
            await ws.send(json.dumps({"action": "subscribe", "type": "matches", "context": context}))

    def _reset_state(self) -> None:
        """Reset the live matches only: statuses survive across reconnects.

        The next ``match_all`` rebuilds the matches; the derived player
        statuses and pending closes are kept so the snapshot diff after
        a reconnect yields exactly the real transitions.
        """
        self._matches = {c: {} for c in _CONTEXTS}
        self._last_snapshot_at = _now_ms()

    def apply_frame(self, raw: str | bytes) -> list[dict[str, Any]]:
        """Fold one raw WS frame; return the resulting transition events.

        Unknown top-level keys are ignored cleanly (the schema may
        evolve); undecodable frames are skipped with a warning.
        """
        try:
            frame = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.warning("undecodable ws frame: %r", raw[:120])
            return []
        if not isinstance(frame, dict):
            return []
        touched: dict[str, dict[str, Any]] = {}
        for context in _CONTEXTS:
            self._apply_context_deltas(context, frame, touched)
        return self._reduce_touched(touched)

    def _apply_context_deltas(
        self, context: str, frame: dict[str, Any], touched: dict[str, dict[str, Any]]
    ) -> None:
        """Fold one context's deltas into the state, touching the changed matches."""
        if f"{context}_match_all" in frame:
            snapshot = _as_match_dict(frame.get(f"{context}_match_all"))
            for stale_id in self._matches[context]:
                if stale_id not in snapshot:
                    touched.setdefault(stale_id, self._matches[context][stale_id])
            self._matches[context] = snapshot
            touched.update(snapshot)
        if f"{context}_match_update" in frame:
            updates = _as_match_dict(frame.get(f"{context}_match_update"))
            self._matches[context].update(updates)
            touched.update(updates)
        removes = frame.get(f"{context}_match_remove")
        if isinstance(removes, list):
            for match_id in removes:
                match = self._matches[context].pop(str(match_id), None)
                if match is not None:
                    touched[str(match_id)] = match
        player_removes = frame.get(f"{context}_player_remove")
        if isinstance(player_removes, list):
            for profile_id in player_removes:
                self._schedule_close(str(profile_id), "", _now_ms())

    def _reduce_touched(self, touched: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        """Derive per-profile transitions from the matches touched by a frame."""
        events: list[dict[str, Any]] = []
        for profile_id in sorted(_profiles_of(touched)):
            status, match_ref = self._current_status(profile_id)
            previous = self._player_status.get(profile_id)
            if status is None:
                if previous is not None:
                    self._schedule_close(profile_id, previous[1], _now_ms())
                continue
            if (status, match_ref) == previous:
                self._pending_closes.pop(profile_id, None)
                continue
            events.append(
                self._transition_event(
                    profile_id, previous[0] if previous else None, status, match_ref
                )
            )
            self._player_status[profile_id] = (status, match_ref)
            self._pending_closes.pop(profile_id, None)
        return events

    def _current_status(self, profile_id: str) -> tuple[str | None, str]:
        """Resolve the current (status, match_ref) of a profile across both contexts.

        Spectate wins over lobby (a launched game supersedes the closing
        lobby); profile ids always compare as ``str`` (heterogeneous
        server-side types, reference §3).
        """
        for context in (CONTEXT_SPECTATE, CONTEXT_LOBBY):
            status = STATUS_GAME if context == CONTEXT_SPECTATE else STATUS_LOBBY
            for match_id, match in self._matches[context].items():
                if profile_id in _profiles_of({match_id: match}):
                    return status, match_id
        return None, ""

    def _transition_event(
        self, profile_id: str, old_status: str | None, status: str, match_ref: str
    ) -> dict[str, Any]:
        """Build one normalized transition event in the MatchEvent shape."""
        event_type = "game_started" if status == STATUS_GAME else "lobby_opened"
        context = CONTEXT_SPECTATE if status == STATUS_GAME else CONTEXT_LOBBY
        metadata: dict[str, str] = {}
        match = self._matches[context].get(match_ref)
        if isinstance(match, dict):
            metadata["match_url"] = (
                "https://www.aoe2lobby.com/w/" if status == STATUS_GAME else "https://www.aoe2lobby.com/j/"
            ) + match_ref
            map_name = match.get("map_name")
            if map_name:
                metadata["map"] = str(map_name)
        if old_status is not None:
            metadata["old_status"] = old_status
        return {
            "match_ref": match_ref,
            "type": event_type,
            "occurred_at": _now_ms(),
            "profile_ids": [profile_id],
            "metadata": metadata,
        }

    def _schedule_close(self, profile_id: str, match_ref: str, now_ms: float) -> None:
        """Park a removal for the grace period before downgrading (§4 trap).

        A lobby removal is followed seconds later by the spectate match
        of the same game; only a removal that survives the full grace
        window becomes a close event.
        """
        self._pending_closes[profile_id] = (match_ref, now_ms)

    def _sweep_pending_closes(self) -> list[dict[str, Any]]:
        """Emit the close events whose grace period has fully elapsed."""
        now = _now_ms()
        events: list[dict[str, Any]] = []
        for profile_id, (match_ref, closed_at) in sorted(self._pending_closes.items()):
            if now - closed_at < self._grace_s * 1000:
                continue
            del self._pending_closes[profile_id]
            status, current_ref = self._current_status(profile_id)
            if status is not None:
                self._player_status[profile_id] = (status, current_ref)
                continue
            previous_status = self._player_status.get(profile_id, ("", ""))[0]
            event_type = "game_ended" if previous_status == STATUS_GAME else "lobby_closed"
            events.append(
                {
                    "match_ref": match_ref,
                    "type": event_type,
                    "occurred_at": now,
                    "profile_ids": [profile_id],
                    "metadata": {},
                }
            )
            self._player_status.pop(profile_id, None)
        return events


def _as_match_dict(payload: object) -> dict[str, dict[str, Any]]:
    """Coerce a ``*_match_all``/``*_match_update`` payload to a match dict.

    ``null`` payloads (observed on the live service) coerce to empty.
    """
    if not isinstance(payload, dict):
        return {}
    return {str(k): v for k, v in payload.items() if isinstance(v, dict)}


def _profiles_of(matches: dict[str, dict[str, Any]]) -> set[str]:
    """Profile ids present in the given matches (occupied player slots)."""
    profiles: set[str] = set()
    for match in matches.values():
        slots = match.get("slots") if isinstance(match, dict) else None
        if not isinstance(slots, dict):
            continue
        for slot in slots.values():
            if isinstance(slot, dict) and slot.get("profileid") not in (None, ""):
                profiles.add(str(slot["profileid"]))
    return profiles


def _now_ms() -> int:
    """Epoch milliseconds, the repo's timestamp convention."""
    return int(time.time() * 1000)
