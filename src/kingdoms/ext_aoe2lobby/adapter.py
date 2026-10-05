"""ext-aoe2lobby live adapter: WebSocket client (aoe2lobby.com).

The live layer (developer direction): aoe2lobby aggregates Worlds Edge
Link lobby data and pushes real-time updates over a WebSocket — lobby
opened, closed, game started. It uses LibreMatch's data underneath, so
anything it reports is cross-checked against ext-librematch (the
source of truth) by the consumer, never trusted blindly here.

Events are mapped to the wire ``MatchEvent`` shape and exposed as an
async iterator; reconnection with backoff is the caller's policy,
implemented once in the server loop.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import websockets

from kingdoms.core.games.aoe2.grace import LobbyClosedGrace

logger = logging.getLogger(__name__)

DEFAULT_WS_URL = "wss://aoe2lobby.com/ws"

_EVENT_TYPES = {
    "lobby_opened": "lobby_opened",
    "lobby_created": "lobby_opened",
    "lobby_closed": "lobby_closed",
    "game_started": "game_started",
    "game_ended": "game_ended",
}


class Aoe2LobbyAdapter:
    """WebSocket client streaming live lobby/game events."""

    def __init__(
        self,
        ws_url: str = DEFAULT_WS_URL,
        reconnect_delay_s: float = 5.0,
        grace: LobbyClosedGrace | None = None,
    ) -> None:
        self._ws_url = ws_url
        self._reconnect_delay_s = reconnect_delay_s
        self._grace = grace or LobbyClosedGrace()

    async def stream_events(self) -> AsyncIterator[dict[str, Any]]:
        """Yield normalized event dicts from the WebSocket, forever.

        Reconnects with a fixed backoff on transport errors and yields
        nothing while disconnected — the stream stays alive, the
        consumer sees silence, not failures.
        """
        import asyncio

        while True:
            try:
                async with websockets.connect(self._ws_url) as ws:
                    async for raw in ws:
                        event = self._normalize(raw)
                        if event is not None:
                            self._track_grace(event)
                            yield event
            except websockets.WebSocketException:
                logger.warning("aoe2lobby ws disconnected; retrying in %ss", self._reconnect_delay_s)
            await asyncio.sleep(self._reconnect_delay_s)

    def _track_grace(self, event: dict[str, Any]) -> None:
        """Feed the adapter-side grace window (reference 5.2 known trap).

        The consumer asks ``grace.should_downgrade`` before acting on a
        lobby_closed; a game_started inside the window cancels it.
        """
        if event["type"] == "lobby_closed":
            self._grace.on_lobby_closed(event["match_ref"])
        elif event["type"] == "game_started":
            self._grace.on_game_started(event["match_ref"])
        elif event["type"] == "game_ended":
            self._grace.forget(event["match_ref"])

    def _normalize(self, raw: str | bytes) -> dict[str, Any] | None:
        """Map one raw WS frame to the normalized event shape.

        Unparseable frames are skipped (warned), unknown event types
        are dropped — the live layer is best-effort by contract.
        """
        try:
            frame = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.warning("undecodable ws frame: %r", raw[:120])
            return None
        if not isinstance(frame, dict):
            return None
        raw_type = str(frame.get("type", frame.get("event", "")))
        event_type = _EVENT_TYPES.get(raw_type)
        if event_type is None:
            return None
        profiles = frame.get("profile_ids", frame.get("players", []))
        if not isinstance(profiles, list):
            profiles = []
        return {
            "match_ref": str(frame.get("match_id", frame.get("lobby_id", ""))),
            "type": event_type,
            "occurred_at": int(frame.get("timestamp", 0)),
            "profile_ids": [str(p) for p in profiles],
            "metadata": {
                str(k): str(v)
                for k, v in frame.items()
                if k not in {"type", "event", "match_id", "lobby_id", "timestamp", "players", "profile_ids"}
            },
        }
