"""ext-aoe2lobby live adapter: WebSocket client (aoe2lobby.com).

Real protocol (validated on a live integration): the server pushes global
state deltas over ``wss://data.aoe2lobby.com/ws/``. Two subscription
channels coexist on one single session:

- ``matches`` (contexts lobby + spectate): global snapshots
  (``*_match_all``) and deltas (``*_match_update``/``*_match_remove``)
  for every observable lobby/game — the reducer filters them
  client-side against the subscribed profile ids;
- ``players``: per-profile ``player_status`` pushes for exactly the
  subscribed profile ids, updated **in place** (subscribe/unsubscribe
  deltas, no session teardown — the site's own frontend does the same).

The adapter owns exactly one WebSocket session at a time: every
disconnection path (transport error, server error, heartbeat timeout,
unexpected exception) falls into the same reconnect loop, which
re-subscribes both channels with the last known profile ids before
resuming the stream. Snapshots after a reconnect rebuild the state; the
derived player statuses survive so the snapshot diff yields exactly the
real transitions.

Known traps covered here (aoe2lobby reference §4/§7): a ~15 s gap
separates ``lobby_match_remove`` from the matching ``spectate_match_*``
when a game launches — a removal is never downgraded before the grace
period elapses; ``*_player_remove`` lists profile ids while
``*_match_remove`` lists match ids; profile ids compare as ``str``;
frames can exceed 1 MB (max_size raised); ``*_all`` payloads can be
``null``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import websockets

from kingdoms.core.games.aoe2.grace import GRACE_PERIOD_S, LobbyClosedGrace
from kingdoms.core.services.provider_endpoints import provider_url as _provider_url

logger = logging.getLogger(__name__)


DEFAULT_WS_URL = _provider_url("aoe2lobby", "ws_url")
SCHEMA_WS_URL = _provider_url("aoe2lobby", "schema_ws_url")
DEFAULT_MAX_SIZE = 10 * 1024 * 1024
DEFAULT_RECONNECT_DELAY_S = 5.0
DEFAULT_HEARTBEAT_S = 90.0  # WS ping interval (transport keepalive)
DEFAULT_REFRESH_S = 60.0

CONTEXT_LOBBY = "lobby"
CONTEXT_SPECTATE = "spectate"
_CONTEXTS = (CONTEXT_LOBBY, CONTEXT_SPECTATE)

STATUS_LOBBY = "lobby"
STATUS_GAME = "game"

EVENT_LOBBY_OPENED = "lobby_opened"
EVENT_LOBBY_CLOSED = "lobby_closed"
EVENT_GAME_STARTED = "game_started"
EVENT_GAME_ENDED = "game_ended"
# a live match's setup changed (civ pick, map, settings, slots) without
# any state transition — the dashboards re-render the lobby's details
EVENT_MATCH_UPDATED = "match_updated"

PLAYER_STATUS_FIELD = "player_status"
STATUS_FIELD = "status"
MATCHID_FIELD = "matchid"
SCHEMA_FIELD = "schema"

DEEP_LINK_URL = "aoe2de://1/-"


class Aoe2LobbyAdapter:
    """One-session WebSocket client reducing aoe2lobby deltas to transitions."""

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
        self._match_payload_fp: dict[str, dict[str, str]] = {c: {} for c in _CONTEXTS}
        self._player_status: dict[str, tuple[str, str]] = {}
        self._close_refs: dict[str, str] = {}
        self._grace = LobbyClosedGrace(grace_s=int(grace_s))
        self._last_snapshot_at = 0
        self._subscribed_ids: set[str] = set()
        self._ws: Any = None
        self._schema: dict[str, Any] = {}
        self._last_frame: dict[str, Any] | None = None

    async def set_subscribed_profiles(self, profile_ids: list[str] | tuple[str, ...]) -> bool:
        """Reconcile the players subscription in place (no session teardown).

        Diffs the requested list against the current subscription and
        sends the subscribe/unsubscribe deltas on the live session;
        the full list is re-sent on the next (re)connect. Returns True
        when applied on a live session, False when queued for the next
        connect (the adapter never opens a second session).
        """
        requested = {str(pid) for pid in profile_ids}
        adds = sorted(requested - self._subscribed_ids)
        removes = sorted(self._subscribed_ids - requested)
        self._subscribed_ids = requested
        if not adds and not removes:
            return True
        if self._ws is None:
            logger.info(
                "aoe2lobby players subscription queued (no session): +%d/-%d",
                len(adds),
                len(removes),
            )
            return False
        # BOTH channels reconcile (the 2026-10-30 lesson): the matches
        # channel is subscribed EMPTY at the boot (the enrollments land
        # seconds later) — a players-only reconciliation leaves it empty
        # forever and the lobbies never arrive. The deltas ride the same
        # subscribe call for both types.
        for profile_id in removes:
            await self._send_subscription("unsubscribe", "players", [profile_id])
        if adds:
            logger.info(
                "aoe2lobby RECONCILE payloads: matches+players +%d: %s",
                len(adds),
                ",".join(adds) if len(adds) <= 25 else f"{adds[0]}...{adds[-1]}",
            )
            await self._send_subscription("subscribe", "matches", adds)
            await self._send_subscription("subscribe", "players", adds)
        logger.info("aoe2lobby subscription updated (matches+players): +%d/-%d", len(adds), len(removes))
        return True

    async def _send_subscription(self, action: str, sub_type: str, ids: list[str]) -> None:
        """Send one subscription delta on the current session (best-effort)."""
        if self._ws is None:
            return
        for context in _CONTEXTS:
            try:
                await self._ws.send(
                    json.dumps({"action": action, "type": sub_type, "context": context, "ids": ids})
                )
            except Exception:
                logger.warning("aoe2lobby %s %s failed (will re-sync on reconnect)", action, sub_type)

    async def fetch_schema(self, timeout_s: float = 15.0) -> dict[str, Any]:
        """Capture the ws schema frame (civ labels, reference §7).

        The schema frame is only pushed on the contextual endpoints
        (``/ws/lobby/``); the main session never carries it. One short
        dedicated connection at bootstrap captures it; failures degrade
        to raw ids (the summaries still render, with numeric civs).
        """
        try:
            async with websockets.connect(SCHEMA_WS_URL, max_size=self._max_size) as ws:
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout_s)
                frame = json.loads(raw)
                while not isinstance(frame, dict) or "schema" not in frame:
                    raw = await asyncio.wait_for(ws.recv(), timeout=timeout_s)
                    frame = json.loads(raw)
                if isinstance(frame.get("schema"), dict):
                    self._schema = frame["schema"]
                    logger.info("aoe2lobby schema captured: %d key(s)", len(self._schema))
        except Exception:
            logger.warning("aoe2lobby schema capture failed — civs stay as raw ids")
        return self._schema

    async def _run_session(self, ws: Any) -> AsyncIterator[dict[str, Any]]:
        """Run one connected session: frames + timed grace closes.

        The reader task feeds raw frames; the sweeper task consumes the
        grace windows on a timer (a lobby exit can leave the session
        silent — the stale-lobby lesson); both meet on one queue the
        generator drains. Ends on the reader's sentinel (server closed)
        or the snapshot refresh (the caller reconnects).
        """
        queue: asyncio.Queue[Any] = asyncio.Queue()



        reader = asyncio.ensure_future(_frame_reader(ws, queue))
        sweeper = asyncio.ensure_future(_grace_sweeper(self, queue))
        try:
            while True:
                item = await queue.get()
                if isinstance(item, str) and not item:
                    break  # the reader ended (sentinel)
                if isinstance(item, dict):
                    yield item  # a grace-close event from the sweeper
                else:
                    for event in self.apply_frame(item):
                        if self._last_frame is not None:
                            await self._persist_last_frame()
                        yield event
                if self._snapshot_due():
                    break
        finally:
            reader.cancel()
            sweeper.cancel()


    def _snapshot_due(self) -> bool:
        """Report whether the periodic snapshot refresh is due."""
        if not self._last_snapshot_at:
            return False
        if _now_ms() - self._last_snapshot_at > self._refresh_s * 1000:
            logger.info("aoe2lobby ws refreshing: reconnect for a fresh snapshot")
            return True
        return False

    def current_state_events(self) -> list[dict[str, Any]]:
        """Build the CURRENT world's events (a new consumer's handshake).

        Every subscribed profile's live status becomes one event (the
        open lobbies, the running games) — a fresh consumer (the game
        service reconnecting, a restart) re-synchronizes instead of
        waiting for the next transition. The per-payload anti-spam
        fingerprints stay untouched: this is the stream's handshake,
        not a tick.
        """
        events: list[dict[str, Any]] = []
        now = _now_ms()
        for profile_id in sorted(self._subscribed_ids):
            previous = self._player_status.get(profile_id)
            if previous is None:
                continue
            status, match_ref = previous
            events.append(
                {
                    "match_ref": match_ref,
                    "type": EVENT_LOBBY_OPENED if status == STATUS_LOBBY else EVENT_GAME_STARTED,
                    "occurred_at": now,
                    "profile_ids": [profile_id],
                    "metadata": self._event_metadata(match_ref, status),
                }
            )
        return events

    def _event_metadata(self, match_ref: str, status: str) -> dict[str, str]:
        """Build the transition metadata for one replayed state event."""
        metadata: dict[str, str] = {}
        if match_ref:
            metadata["match_url"] = (
                "https://www.aoe2lobby.com/w/" if status == STATUS_GAME else "https://www.aoe2lobby.com/j/"
            ) + match_ref
            summary = self.match_summary(match_ref, status)
            for key, value in summary.items():
                if value not in ("", None):
                    metadata[str(key)] = str(value)
        return metadata

    async def stream_events(self) -> AsyncIterator[dict[str, Any]]:
        """Yield normalized event dicts from the WebSocket, forever.

        Exactly one session is open at any time. Every failure path —
        transport error, server-side close, heartbeat timeout,
        unexpected exception — falls into the same reconnect loop:
        fixed backoff, re-subscribe both channels with the last known
        profile ids, then resume. Yields nothing while disconnected:
        the stream stays alive, the consumer sees silence, not
        failures. The state is kept across reconnects so the snapshot
        diff yields exactly the real transitions (deltas may be sparse
        server-side).
        """
        while True:
            try:
                async with websockets.connect(
                    self._ws_url,
                    max_size=self._max_size,
                    ping_interval=self._heartbeat_s,
                    ping_timeout=30.0,
                ) as ws:
                    self._ws = ws
                    self._reset_matches()
                    if not self._schema:
                        await self.fetch_schema()
                    await self._subscribe_all(ws)
                    logger.info("aoe2lobby ws connected: %s", self._ws_url)
                    async for event in self._run_session(ws):
                        yield event
            except (TimeoutError, websockets.WebSocketException, OSError):
                logger.warning("aoe2lobby ws disconnected; retrying in %ss", self._reconnect_delay_s)
            except Exception:
                logger.exception("aoe2lobby ws unexpected error; retrying in %ss", self._reconnect_delay_s)
            finally:
                self._ws = None
            await asyncio.sleep(self._reconnect_delay_s)

    async def _subscribe_all(self, ws: Any) -> None:
        """Subscribe both channels scoped to the enrolled profile ids.

        Neither subscription is global: the enrolled ids ride every
        subscribe (matches and players). Live-validated: the players
        channel alone pushes nothing — the states live in the matches
        snapshots and deltas; the ids passed to the matches channel
        are the server-side scope, and the strict enrolled-ids filter
        stays the reducer's leak guard (the 111-pages lesson) whatever
        the server actually honors.
        """
        ids = sorted(self._subscribed_ids)
        logger.info(
            "aoe2lobby SUBSCRIBE: %d profile(s): %s",
            len(ids),
            ",".join(ids) if ids else "(EMPTY)",
        )
        for context in _CONTEXTS:
            payload = {"action": "subscribe", "type": "matches", "context": context, "ids": ids}
            logger.info("aoe2lobby SUBSCRIBE payload: %s", json.dumps(payload))
            await ws.send(json.dumps(payload))
        for context in _CONTEXTS:
            payload = {"action": "subscribe", "type": "players", "context": context, "ids": ids}
            logger.info("aoe2lobby SUBSCRIBE payload: %s", json.dumps(payload)[:500])
            await ws.send(json.dumps(payload))
        if ids:
            await self._send_subscription("subscribe", "players", ids)
            logger.info(
                "aoe2lobby session ready: matches + players, %d profile(s) enrolled",
                len(ids),
            )

    def _reset_matches(self) -> None:
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
        schema = frame.get(SCHEMA_FIELD)
        if isinstance(schema, dict):
            self._schema = schema
            logger.info("aoe2lobby schema captured: %d key(s)", len(schema))
            return []
        # every match-carrying frame logs (the owner's tick visibility):
        # what the websocket actually sends, when
        keys = sorted(k for k in frame if "_match_" in k or k == PLAYER_STATUS_FIELD)
        if keys:
            sizes = {k: len(frame.get(k) or {}) for k in keys if isinstance(frame.get(k), (dict, list))}
            if sizes:
                logger.info("aoe2lobby FRAME: %s", " ".join(f"{k}={v}" for k, v in sizes.items()))
        events = self._reduce_player_status(frame.get(PLAYER_STATUS_FIELD))
        if events:
            self._log_events("player_status push", events)
            return events
        touched: dict[str, dict[str, Any]] = {}
        for context in _CONTEXTS:
            self._apply_context_deltas(context, frame, touched)
        events = self._reduce_touched(touched)
        self._log_events("match delta", events)
        self._last_frame = {"raw": str(raw)[:20000], "frame": frame, "events": events, "at": _now_ms()}
        return events

    async def _persist_last_frame(self) -> None:
        """Persist the last received frame + its derived info (Mongo).

        The owner's observability ask: the provider's `ws_last_frame`
        collection holds the newest raw frame and its derived events —
        the debugging trail reads the base instead of guessing what
        the websocket sent. Best-effort: a store failure never breaks
        the stream.
        """
        import os

        uri = os.environ.get("MONGO_URI", "")
        buffered = self._last_frame
        if not uri or buffered is None:
            return
        try:
            from kingdoms.core.models.db import get_async_database

            database = get_async_database()
            frame = buffered.get("frame") or {}
            keys = sorted(k for k in frame if "_match_" in k or k == PLAYER_STATUS_FIELD)
            document = {
                "_id": "last",
                "at": buffered.get("at", _now_ms()),
                "keys": keys,
                "raw": buffered.get("raw", ""),
                "derived_events": [
                    {"type": e.get("type"), "match": e.get("match_ref"), "profiles": list(e.get("profile_ids", []))}
                    for e in buffered.get("events", [])
                ],
            }
            await database["ws_last_frame"].replace_one({"_id": "last"}, document, upsert=True)
        except Exception:
            logger.debug("ws last-frame persist skipped — best-effort", exc_info=True)

    def _log_events(self, source: str, events: list[dict[str, Any]]) -> None:
        """Log every emitted transition — the pod's observable trail.

        One INFO line per event: the profile ids, the transition and
        the match — 'AOE2LOBBY EVENT player=848567 match=123 lobby_opened'
        reads straight in `docker logs`.
        """
        for event in events:
            logger.info(
                "aoe2lobby EVENT %s: profiles=%s match=%s type=%s%s",
                source,
                ",".join(str(p) for p in event.get("profile_ids", [])),
                event.get("match_ref", ""),
                event.get("type", "?"),
                f" map={event['metadata'].get('map', '')}" if event.get("metadata", {}).get("map") else "",
            )

    def _reduce_player_status(self, statuses: object) -> list[dict[str, Any]]:
        """Fold one ``player_status`` push for the subscribed profiles.

        Shape per the aoe2lobby protocol: ``{profile_id: {status,
        matchid, steam_lobbyid}}`` with status "lobby"/"playing" (or
        null once the player leaves). The match url derives from the
        status; a matchid change is a transition even at constant
        status (player switched lobby).
        """
        if not isinstance(statuses, dict):
            return []
        events: list[dict[str, Any]] = []
        for profile_id, info in statuses.items():
            if not isinstance(info, dict):
                continue
            events.extend(self._apply_player_status(str(profile_id), info))
        return events

    def _apply_player_status(self, profile_id: str, info: dict[str, Any]) -> list[dict[str, Any]]:
        """Derive transitions from one player's pushed status entry."""
        if profile_id not in self._subscribed_ids:
            return []
        status = info.get(STATUS_FIELD)
        match_ref = str(info.get(MATCHID_FIELD) or "")
        if status == STATUS_LOBBY:
            return self._record_status(profile_id, STATUS_LOBBY, match_ref)
        if status in ("playing", "spectate", "game"):
            return self._record_status(profile_id, STATUS_GAME, match_ref)
        return self._record_status(profile_id, None, match_ref)

    def _record_status(
        self, profile_id: str, status: str | None, match_ref: str
    ) -> list[dict[str, Any]]:
        """Record one derived status and emit the transition when it changed."""
        previous = self._player_status.get(profile_id)
        if status is None:
            if previous is None:
                return []
            self._schedule_close(profile_id, previous[1], _now_ms())
            return []
        if (status, match_ref) == previous:
            self._cancel_close(profile_id)
            return []
        self._cancel_close(profile_id)
        event = self._transition_event(
            profile_id, previous[0] if previous else None, status, match_ref
        )
        self._player_status[profile_id] = (status, match_ref)
        return [event]

    def _apply_context_deltas(
        self, context: str, frame: dict[str, Any], touched: dict[str, dict[str, Any]]
    ) -> None:
        """Fold one context's deltas into the state, touching the changed matches."""
        if f"{context}_match_all" in frame:
            self._apply_match_all(context, frame.get(f"{context}_match_all"), touched)
        if f"{context}_match_update" in frame:
            self._apply_match_updates(context, frame.get(f"{context}_match_update"), touched)
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

    def _apply_match_all(
        self, context: str, payload: object, touched: dict[str, dict[str, Any]]
    ) -> None:
        """Fold a full snapshot: replace the matches, touch only the CHANGED.

        The periodic snapshot is the refresh tick (owner direction): the
        interesting match ids are the ones carrying an enrolled profile;
        a match re-emits only when its DISPLAYED payload changed since
        the last tick — an unchanged roster is silent (the boot must not
        replay the whole world, the spammer's lesson).
        """
        snapshot = _as_match_dict(payload)
        for stale_id in self._matches[context]:
            if stale_id not in snapshot:
                # gone from the feed: the close path handles it (the
                # result fetch rides the offline transition)
                touched.setdefault(stale_id, self._matches[context][stale_id])
                self._match_payload_fp[context].pop(stale_id, None)
        new_fingerprints: dict[str, str] = {}
        for mid, match in snapshot.items():
            fp = _payload_fingerprint(match)
            new_fingerprints[mid] = fp
            if self._match_payload_fp[context].get(mid) != fp:
                touched.setdefault(mid, match)
        self._matches[context] = snapshot
        self._match_payload_fp[context] = new_fingerprints

    def _apply_match_updates(
        self, context: str, payload: object, touched: dict[str, dict[str, Any]]
    ) -> None:
        """Fold one delta batch: touch only the payloads that changed."""
        updates = _as_match_dict(payload)
        self._matches[context].update(updates)
        for mid, match in updates.items():
            fp = _payload_fingerprint(match)
            if self._match_payload_fp[context].get(mid) != fp:
                # a live change (setup, civs, slots): re-emit even
                # without a state transition
                touched.setdefault(mid, match)
                self._match_payload_fp[context][mid] = fp

    def _reduce_touched(self, touched: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        """Derive per-profile transitions from the matches touched by a frame."""
        events: list[dict[str, Any]] = []
        for profile_id in sorted(_profiles_of(touched)):
            if profile_id not in self._subscribed_ids:
                continue
            status, match_ref = self._current_status(profile_id)
            previous = self._player_status.get(profile_id)
            if status is None:
                if previous is not None:
                    self._schedule_close(profile_id, previous[1], _now_ms())
                continue
            if (status, match_ref) == previous:
                self._cancel_close(profile_id)
                # same state, same match: a touched entry means the RAW
                # payload changed (civ pick, map, settings, slots) —
                # re-emit ONLY while the lobby is open; once the game
                # launched the settings are frozen (owner direction: no
                # in-game update noise)
                if (
                    status == STATUS_LOBBY
                    and match_ref in touched
                    and profile_id in _profiles_of({match_ref: touched[match_ref]})
                ):
                    events.append(self._match_updated_event(profile_id, status, match_ref))
                continue
            events.append(
                self._transition_event(
                    profile_id, previous[0] if previous else None, status, match_ref
                )
            )
            self._player_status[profile_id] = (status, match_ref)
            self._cancel_close(profile_id)
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
        """Build one normalized transition event in the MatchEvent shape.

        The metadata carries the full match summary (map, format, civs,
        teams, host, join/spectate links, aoe2de deep link) so consumers
        render rich notifications without any extra provider call.
        """
        event_type = EVENT_GAME_STARTED if status == STATUS_GAME else EVENT_LOBBY_OPENED
        metadata: dict[str, str] = {}
        if match_ref:
            metadata["match_url"] = (
                "https://www.aoe2lobby.com/w/" if status == STATUS_GAME else "https://www.aoe2lobby.com/j/"
            ) + match_ref
            summary = self.match_summary(match_ref, status)
            for key, value in summary.items():
                if value not in ("", None):
                    metadata[str(key)] = str(value)
        if old_status is not None:
            metadata["old_status"] = old_status
        return {
            "match_ref": match_ref,
            "type": event_type,
            "occurred_at": _now_ms(),
            "profile_ids": [profile_id],
            "metadata": metadata,
        }

    def _match_updated_event(
        self, profile_id: str, status: str, match_ref: str
    ) -> dict[str, Any]:
        """Build one match_updated event (state unchanged, setup changed).

        The metadata carries the fresh summary so the dashboards
        re-render the lobby's details.
        """
        metadata: dict[str, str] = {}
        if match_ref:
            metadata["match_url"] = (
                "https://www.aoe2lobby.com/w/" if status == STATUS_GAME else "https://www.aoe2lobby.com/j/"
            ) + match_ref
            summary = self.match_summary(match_ref, status)
            for key, value in summary.items():
                if value not in ("", None):
                    metadata[str(key)] = str(value)
        return {
            "match_ref": match_ref,
            "type": EVENT_MATCH_UPDATED,
            "occurred_at": _now_ms(),
            "profile_ids": [profile_id],
            "metadata": metadata,
        }

    def _civ_label(self, civ_id: object) -> str:
        """Resolve a civilization id to its label through the ws schema.

        The server pushes a ``schema`` frame on connect (wsSchema.json,
        reference §7); civilization maps civ ids to labels. Falls back
        to the raw id when the schema is missing or the id unknown.
        """
        raw = str(civ_id if civ_id not in (None, "") else "")
        if not raw:
            return ""
        civs = self._schema.get("civilization")
        if isinstance(civs, dict):
            label = civs.get(raw) or civs.get(int(raw)) if raw.isdigit() else civs.get(raw)
            if isinstance(label, str) and label:
                return label
        return raw

    def _match_type_label(self, match: dict[str, Any]) -> str:
        """Resolve the game mode label (RandomMap, FFA, ...) via the schema.

        The match's ``match_typeid`` indexes the ws schema's
        ``match_typeid`` map; the format (1v1, 2v2...) stays separate —
        the label names the MODE, the format the team layout.
        """
        raw = match.get("match_typeid")
        if raw in (None, ""):
            return ""
        types = self._schema.get("match_typeid")
        if isinstance(types, dict):
            label = types.get(str(raw)) or (types.get(int(raw)) if str(raw).isdigit() else None)
            if isinstance(label, str) and label:
                return label
        return str(raw)

    def match_summary(self, match_ref: str, status: str) -> dict[str, Any]:
        """Build the enriched summary of a match for the given context.

        Covers the dashboard requirements: map name, format (1v1, 2v2,
        FFA — derived from the team distribution), slots, host, per-slot
        civs, aoe2lobby join/spectate URLs and the aoe2de:// deep link
        (joinable lobbies only).
        """
        context = CONTEXT_SPECTATE if status == STATUS_GAME else CONTEXT_LOBBY
        match = self._matches[context].get(match_ref)
        if not isinstance(match, dict):
            return {}
        slots = self._match_slots(match)
        players: list[dict[str, Any]] = []
        teams: dict[int, int] = {}
        for slot in slots:
            profile_id = slot.get("profileid")
            if profile_id in (None, ""):
                continue
            team = int(slot.get("team") or 0)
            teams[team] = teams.get(team, 0) + 1
            players.append(
                {
                    "profile_id": str(profile_id),
                    "name": str(slot.get("name") or ""),
                    "civilization": self._civ_label(slot.get("civilization")),
                    "avatar": str(slot.get("steam_avatar") or ""),
                    "country": str(slot.get("country") or ""),
                    "team": team,
                }
            )
        host_profile = str(match.get("host_profileid") or "")
        host = next((p for p in players if p["profile_id"] == host_profile), None)
        return {
            "map": str(match.get("map_name") or ""),
            "format": _format_label(teams, len(players)),
            "match_type": self._match_type_label(match),
            "status": status,
            "slots_taken": str(match.get("slots_taken", len(players))),
            "slots_total": str(match.get("slots_total", 0)),
            "host": str(host["name"]) if host else "",
            "players": json.dumps(
                [
                    {
                        "profile_id": p["profile_id"],
                        "name": p["name"],
                        "civ": p["civilization"],
                        "avatar": p["avatar"],
                        "country": p["country"],
                        "team": p["team"],
                    }
                    for p in players
                ]
            ),
            "lobby_url": f"https://www.aoe2lobby.com/j/{match_ref}",
            "spectate_url": f"https://www.aoe2lobby.com/w/{match_ref}",
            "deep_link": f"{DEEP_LINK_URL}{match_ref}" if status != STATUS_GAME else "",
        }

    def _match_slots(self, match: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract the occupied slot dicts of one match payload."""
        slots = match.get("slots")
        if not isinstance(slots, dict):
            return []
        return [s for s in slots.values() if isinstance(s, dict) and s.get("status") == 3]

    def _schedule_close(self, profile_id: str, match_ref: str, now_ms: float) -> None:
        """Park a removal behind the game module's grace window (§4 trap).

        The window rule lives once in ``LobbyClosedGrace`` (the game
        module owns it); this adapter only keeps the profile→match_ref
        association for the eventual close event payload.
        """
        self._grace.on_lobby_closed(profile_id, now=now_ms / 1000.0)
        self._close_refs[profile_id] = match_ref

    def _cancel_close(self, profile_id: str) -> None:
        """Drop a pending close: the player was seen again (§9 last-state rule)."""
        self._grace.forget(profile_id)
        self._close_refs.pop(profile_id, None)

    def _sweep_pending_closes(self) -> list[dict[str, Any]]:
        """Emit the close events whose grace period has fully elapsed."""
        now = _now_ms()
        events: list[dict[str, Any]] = []
        for profile_id in sorted(self._close_refs):
            if not self._grace.should_downgrade(profile_id, now=now / 1000.0):
                continue
            match_ref = self._close_refs.pop(profile_id)
            self._grace.forget(profile_id)
            status, current_ref = self._current_status(profile_id)
            if status is not None:
                self._player_status[profile_id] = (status, current_ref)
                continue
            previous_status = self._player_status.get(profile_id, ("", ""))[0]
            event_type = EVENT_GAME_ENDED if previous_status == STATUS_GAME else EVENT_LOBBY_CLOSED
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


def _payload_fingerprint(match: dict[str, Any]) -> str:
    """Fingerprint one raw match payload (stable, setup-change sensitive)."""
    try:
        return hashlib.sha256(
            json.dumps(match, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    except (TypeError, ValueError):
        return str(_now_ms())


def _as_match_dict(payload: object) -> dict[str, dict[str, Any]]:
    """Coerce a ``*_match_all``/``*_match_update`` payload to a match dict.

    ``null`` payloads (observed on the live service) coerce to empty.
    """
    if not isinstance(payload, dict):
        return {}
    return {str(k): v for k, v in payload.items() if isinstance(v, dict)}


def _profiles_of(matches: dict[str, dict[str, Any]]) -> set[str]:
    """Profile ids present in the given matches (occupied player slots).

    Only status 3 (Player) slots count: Open/Closed/AI slots can carry a
    residual profileid that is not a present player (reference §5).
    """
    profiles: set[str] = set()
    for match in matches.values():
        slots = match.get("slots") if isinstance(match, dict) else None
        if not isinstance(slots, dict):
            continue
        for slot in slots.values():
            if (
                isinstance(slot, dict)
                and slot.get("status") == 3
                and slot.get("profileid") not in (None, "")
            ):
                profiles.add(str(slot["profileid"]))
    return profiles


def _format_label(teams: dict[int, int], player_count: int) -> str:
    """Derive the human match format from the team distribution.

    All distinct teams of equal size read as ``NvN``; anything else
    (mixed sizes, no teams, one team) reads as FFA.
    """
    sizes = sorted(teams.values())
    if player_count > 1 and sizes and len(set(sizes)) == 1 and len(sizes) > 1:
        return f"{sizes[0]}v{sizes[0]}"
    if player_count == 1:
        return "1v1" if not teams else "solo"
    return f"FFA ({player_count}p)" if player_count else ""


def _now_ms() -> int:
    """Epoch milliseconds, the repo's timestamp convention."""
    return int(time.time() * 1000)


async def _frame_reader(ws: Any, queue: asyncio.Queue[Any]) -> None:
    """Feed raw frames to the queue until the session ends."""
    try:
        while True:
            queue.put_nowait(await ws.recv())
    except Exception:
        queue.put_nowait("")


async def _grace_sweeper(adapter: Aoe2LobbyAdapter, queue: asyncio.Queue[Any]) -> None:
    """Consume the grace windows on a timer (silence-proof)."""
    while True:
        await asyncio.sleep(2.0)
        for event in adapter._sweep_pending_closes():
            queue.put_nowait(event)
