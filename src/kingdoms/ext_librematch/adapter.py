"""ext-librematch live adapter: Worlds Edge Link Community API client.

Source of truth for AoE2 reference data (developer direction): lobby
listings come from the public Community API endpoint documented by the
LibreMatch wiki (``GET /community/advertisement/findAdvertisements``).
The slotinfo/options fields of each lobby are base64+zlib blobs decoded
by the game module (``kingdoms.core.games.aoe2.blobs``).

Conservative by design: on any decode or transport failure the adapter
serves what it has and degrades — never fails the provider process.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urljoin

import httpx

from kingdoms.core.debug import capture
from kingdoms.core.games.aoe2.blobs import BlobDecodeError, decode_blob
from kingdoms.core.models.game import (
    GameMap,
    MatchDetails,
    MatchResult,
    PlayerStats,
    Slot,
    StatsBlock,
    StatsEntry,
)
from kingdoms.core.rpc.rate_limit import ProviderRateLimiter
from kingdoms.core.services.provider_endpoints import provider_url

logger = logging.getLogger(__name__)


DEFAULT_BASE_URL = provider_url("librematch", "api_url")
LOBBIES_PATH = "/community/advertisement/findAdvertisements"
LEADERBOARDS_PATH = "/api/leaderboard"
PERSONAL_STAT_PATH = "/community/leaderboard/GetPersonalStat"
RECENT_MATCH_PATH = "/community/leaderboard/GetRecentMatchHistory"
LOBBIES_QUERY_PARAMS = {"title": "age2"}
LEADERBOARD_KEYS = {"0": "rm_1v1", "1": "rm_team", "2": "unranked", "3": "dm_1v1", "4": "dm_team"}

_SLOT_KINDS = {True: "human", False: "open"}


def _matching_entry(
    containers: list[Any],
    profile_id: str,
    nested_key: str = "",
) -> dict[str, Any]:
    """Find the entry (or nested member) matching one profile id."""
    for container in containers:
        candidates: list[Any] = []
        if nested_key:
            candidates = (container or {}).get(nested_key, []) or []
        elif isinstance(container, dict):
            candidates = [container]
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            if str(candidate.get("profile_id", "")) == str(profile_id):
                return candidate
    return {}


class LibrematchAdapter:
    """HTTP client for the Worlds Edge Link Community API (lobbies).

    Every external call goes through the Redis-backed throttle
    (``game:{game_key}:rate``, reference 2.4); over budget degrades to
    an empty answer, never an error.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout_s: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
        api_key: str = "",
        rate_limiter: ProviderRateLimiter | None = None,
    ) -> None:
        self._base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._timeout_s = timeout_s
        self._transport = transport
        self._api_key = api_key
        self._rate_limiter = rate_limiter

    async def _allowed(self) -> bool:
        """Consume one throttle slot; no limiter configured means allowed."""
        if self._rate_limiter is None:
            return True
        return await self._rate_limiter.check()

    async def fetch_lobbies(self) -> list[dict[str, Any]]:
        """Fetch the current public lobby listings (raw API payloads)."""
        if not await self._allowed():
            logger.warning("librematch call over rate budget; degrading to empty lobby list")
            return []
        async with httpx.AsyncClient(timeout=self._timeout_s, transport=self._transport) as client:
            reply = await client.get(
                urljoin(self._base_url + "/", LOBBIES_PATH.lstrip("/")),
                params=LOBBIES_QUERY_PARAMS,
            )
            reply.raise_for_status()
            payload = reply.json()
        if isinstance(payload, list):
            items = payload
        else:
            items = payload.get("matches", payload.get("result", []))
        return [item for item in items if isinstance(item, dict)]

    async def match_details(self, match_ref: str) -> MatchDetails | None:
        """Build MatchDetails for a lobby, decoding slotinfo/options blobs.

        Cross-checks the advertisement list for the referenced lobby;
        returns None when the lobby is not visible (expired or unknown).
        """
        for lobby in await self.fetch_lobbies():
            if str(lobby.get("advertiserId", lobby.get("match_id", ""))) != match_ref:
                continue
            return self._to_match_details(lobby)
        return None

    async def list_maps(self) -> list[GameMap]:
        """Serve the map catalog seen across the current lobbies.

        The Community API exposes no dedicated map catalog; the distinct
        maps of the live lobbies are the best available catalog view.
        """
        maps: dict[str, GameMap] = {}
        for lobby in await self.fetch_lobbies():
            name = str(lobby.get("mapname", "") or lobby.get("mapName", ""))
            if not name or name in maps:
                continue
            maps[name] = GameMap(map_key=name, name=name, map_type="lobby")
        return list(maps.values())

    async def player_stats(self, profile_id: str) -> PlayerStats | None:
        """Fetch a profile's identity and leaderboard stats.

        Calls the community leaderboard endpoint — it is PUBLIC (no API
        key; the 2026-10-16 lesson: the key gate rejected every profile
        while the endpoint answers anyone):
        ``/community/leaderboard/GetPersonalStat?title=age2&profile_ids=[...]``.
        The reply carries the profile's identity (``statGroups[].members[]``
        — alias, country, clan) plus one stats entry per requested
        leaderboard (``leaderboardStats[]``). Transport failures degrade
        to None — the provider never fails the serving process; the
        Redis-backed rate budget is the only throttle.
        """
        if not profile_id:
            return None
        if not await self._allowed():
            logger.warning("librematch call over rate budget; degrading stats to None")
            return None
        blocks: list[StatsBlock] = []
        async with httpx.AsyncClient(timeout=self._timeout_s, transport=self._transport) as client:
            for board_id, board_name in LEADERBOARD_KEYS.items():
                try:
                    reply = await client.get(
                        urljoin(self._base_url + "/", PERSONAL_STAT_PATH.lstrip("/")),
                        params={"title": "age2", "profile_ids": f'["{profile_id}"]', "leaderboard_id": board_id},
                    )
                    reply.raise_for_status()
                    payload = reply.json()
                except Exception:
                    logger.warning("leaderboard fetch failed (board %s)", board_id, exc_info=True)
                    continue
                entries = self._parse_leaderboard_entry(payload, profile_id)
                if entries:
                    blocks.append(StatsBlock(name=board_name, entries=tuple(entries)))
        if not blocks:
            return None
        return PlayerStats(profile_id=profile_id, blocks=tuple(blocks))




    async def recent_match_result(
        self, profile_ids: list[str], since_epoch_s: int
    ) -> MatchResult | None:
        """Fetch the latest finished match of the profiles (WorldsEdge).

        ``GetRecentMatchHistory`` lists a profile's recent finished
        matches with the full outcome (winner, duration, map, elo
        deltas). The WorldsEdge match id is NOT the aoe2lobby ref:
        the correlation rides the profiles and the time window —
        the most recent match whose start follows ``since`` and whose
        members include one of the ``profile_ids``.

        Returns None on transport failure or when nothing matches
        (the upstream has not recorded the match yet — the caller
        retries with a backoff).
        """
        if not profile_ids:
            return None
        probe = profile_ids[0]
        if not await self._allowed():
            logger.warning("librematch call over rate budget; degrading result to None")
            return None
        async with httpx.AsyncClient(timeout=self._timeout_s, transport=self._transport) as client:
            reply = await client.get(
                urljoin(self._base_url + "/", RECENT_MATCH_PATH.lstrip("/")),
                params={"title": "age2", "profile_ids": f'["{probe}"]', "count": "5"},
            )
            reply.raise_for_status()
            payload = reply.json()
        matches = payload.get("matchHistoryStats") or []
        wanted = {str(pid) for pid in profile_ids}
        best: dict[str, Any] | None = None
        for match in matches:
            members = match.get("matchhistorymember") or []
            member_ids = {str(m.get("profile_id")) for m in members if isinstance(m, dict)}
            if not (member_ids & wanted):
                continue
            started = int(match.get("startgametime") or 0)
            if started < since_epoch_s:
                continue
            if best is None or started > int(best.get("startgametime") or 0):
                best = match
        if best is None:
            return None
        return self._to_match_result(best)

    def _to_match_result(self, match: dict[str, Any]) -> MatchResult:
        """Build the MatchResult from one WorldsEdge history entry."""
        members = match.get("matchhistorymember") or []
        winners: list[str] = []
        losers: list[str] = []
        teams: dict[str, int] = {}
        ratings: dict[str, tuple[int, int]] = {}
        for member in members:
            if not isinstance(member, dict):
                continue
            pid = str(member.get("profile_id", ""))
            if member.get("outcome") == 1:
                winners.append(pid)
            else:
                losers.append(pid)
            teams[pid] = int(member.get("teamid") or 0)
            old = int(member.get("oldrating") or 0)
            new = int(member.get("newrating") or 0)
            if old or new:
                ratings[pid] = (old, new)
        started = int(match.get("startgametime") or 0)
        finished = int(match.get("completiontime") or 0)
        return MatchResult(
            match_ref=str(match.get("id", "")),
            winner_profile_id=winners[0] if len(winners) == 1 else ",".join(winners),
            loser_profile_ids=tuple(losers),
            map_name=str(match.get("mapname") or "").removesuffix(".rms"),
            duration_ms=max(0, (finished - started)) * 1000,
            ended_at=finished * 1000,
            factions={},
            found=True,
            ratings=ratings,
            teams=teams,
        )

    def _parse_leaderboard_entry(self, payload: dict[str, Any], profile_id: str) -> list[StatsEntry]:
        """Extract one profile's entries from a GetPersonalStat reply.

        The identity lives in ``statGroups[].members[]`` (the member whose
        ``profile_id`` matches); the per-board stats in ``leaderboardStats[]``
        (each carrying ``profile_id`` alongside rank/rating/wins/losses).
        A profile present in ``statGroups`` but absent from
        ``leaderboardStats`` still yields its identity entries (alias,
        country, clan) — the registration validation only needs the
        profile to exist.
        """
        member = _matching_entry(payload.get("statGroups", []) or [], profile_id, "members")
        stat = _matching_entry(payload.get("leaderboardStats", []) or [], profile_id)
        if not member and not stat:
            return []
        entries: list[StatsEntry] = []
        if member:
            alias = str(member.get("alias", "") or member.get("name", "") or "")
            entries.append(StatsEntry(key="alias", value=alias))
            entries.append(StatsEntry(key="country", value=str(member.get("country", "") or "")))
            entries.append(StatsEntry(key="clan", value=str(member.get("clanlist_name", "") or "")))
        if stat:
            entries.extend(
                [
                    StatsEntry(key="rank", value=str(stat.get("rank", ""))),
                    StatsEntry(key="rating", value=str(stat.get("rating", stat.get("elo", "")))),
                    StatsEntry(key="wins", value=str(stat.get("wins", ""))),
                    StatsEntry(key="losses", value=str(stat.get("losses", ""))),
                    StatsEntry(key="streak", value=str(stat.get("streak", ""))),
                    StatsEntry(key="games", value=str(stat.get("games", ""))),
                ]
            )
        return [e for e in entries if e.value]

    def _to_match_details(self, lobby: dict[str, Any]) -> MatchDetails:
        """Map one raw lobby payload to MatchDetails, blobs decoded.

        Blob decode failures degrade to an empty slotinfo/options set —
        the raw blob stays available on the source payload, and the
        provider keeps serving the rest of the match data.
        """
        slots_raw: list[Slot] = []
        options_raw: tuple[tuple[str, str], ...] = ()
        for blob_field, target in (("slotinfo", "slots"), ("options", "options")):
            blob = lobby.get(blob_field)
            if not isinstance(blob, str):
                continue
            try:
                decoded = decode_blob(blob)
            except BlobDecodeError as exc:
                logger.warning("undecodable %s blob for lobby %s", blob_field, lobby.get("match_id"))
                capture(
                    "blob.decode_failed",
                    blob_field=blob_field,
                    match_id=lobby.get("match_id"),
                    error=str(exc),
                    blob_head=blob[:120],
                )
                continue
            if target == "slots":
                if isinstance(decoded, list):
                    slots_raw = self._parse_slots(decoded)
            elif isinstance(decoded, dict):
                options_raw = tuple((str(k), str(v)) for k, v in decoded.items() if not k.startswith("_"))
        return MatchDetails(
            match_ref=str(lobby.get("advertiserId", lobby.get("match_id", ""))),
            map_name=str(lobby.get("mapname", lobby.get("mapName", ""))),
            slots=tuple(slots_raw),
            options=options_raw,
            started_at=0,
            match_kind="lobby",
        )

    def _parse_slots(self, entries: list[dict[str, object]]) -> list[Slot]:
        """Parse the decoded slotinfo slot objects into per-slot models.

        Slot keys follow the Worlds Edge slotinfo format (profileInfo.id,
        factionID, teamID); the friendlier slot_index/civ keys used by
        earlier tests are still accepted.
        """
        if not isinstance(entries, list):
            return []
        slots: list[Slot] = []
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            civ = entry.get("civ", entry.get("civilization", entry.get("factionID", 0)))
            filled = bool(
                entry.get("filled", "profile_id" in entry or "playerId" in entry or "profileInfo.id" in entry)
            )
            slots.append(
                Slot(
                    slot_index=int(str(entry.get("slot_index", entry.get("stationID", index)) or "0")),
                    profile_id=str(entry.get("profile_id", entry.get("playerId", entry.get("profileInfo.id", "")))),
                    faction_key=str(civ),
                    team=int(str(entry.get("team", entry.get("teamID", 0)) or "0")),
                    filled=filled,
                    slot_kind=str(entry.get("slot_kind", "")) or _SLOT_KINDS[filled],
                )
            )
        return slots
