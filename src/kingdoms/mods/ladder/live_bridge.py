"""Live snapshot bridge over the aoe2lobby WebSocket event stream.

The lobby adapter yields push events (lobby opened/closed, game
started/ended) — there is no request/response ``fetch``. This bridge
consumes the stream as a background task and keeps the latest known
state per ``match_ref`` so the ladder can serve ``fetch_match`` from the
snapshot (the 5s live TTL in the provider cache paces re-reads).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class Aoe2LobbySnapshotBridge:
    """MatchDataProvider fed by the aoe2lobby event stream."""

    provider_key = "aoe2lobby"

    def __init__(self, stream_events: Any) -> None:
        self._stream_events = stream_events
        self._snapshots: dict[str, dict[str, Any]] = {}
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Start consuming the event stream in the background."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._consume())

    async def stop(self) -> None:
        """Cancel the consumer task, if any."""
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _consume(self) -> None:
        """Fold stream events into per-match snapshots, forever."""
        async for event in self._stream_events():
            try:
                self._apply(event)
            except Exception:
                logger.warning("lobby snapshot update failed", exc_info=True)

    def _apply(self, event: dict[str, Any]) -> None:
        """Fold one normalized event into the snapshot of its match."""
        match_ref = str(event.get("match_ref", ""))
        if not match_ref:
            return
        kind = str(event.get("type", ""))
        occurred_at = int(event.get("occurred_at", 0))
        metadata = dict(event.get("metadata") or {})
        snapshot = self._snapshots.setdefault(
            match_ref,
            {"match_ref": match_ref, "slots": [], "options": {}, "started_at": 0, "match_kind": "lobby"},
        )
        profiles = [str(p) for p in event.get("profile_ids", [])]
        if profiles:
            snapshot["slots"] = [{"slot_index": i, "profile_id": p, "filled": True} for i, p in enumerate(profiles)]
        snapshot.update({str(k): v for k, v in metadata.items()})
        if kind == "game_started" and occurred_at:
            snapshot["started_at"] = occurred_at
        if kind == "game_ended" and occurred_at:
            snapshot["ended_at"] = occurred_at
        if kind == "lobby_opened" and metadata.get("mapname"):
            snapshot["map_name"] = str(metadata["mapname"])
        if kind == "lobby_closed":
            snapshot["closed"] = True

    async def fetch_match(self, match_ref: str) -> dict[str, Any] | None:
        """Serve the latest snapshot of a match, None when unknown."""
        if not match_ref:
            return None
        return self._snapshots.get(match_ref)
