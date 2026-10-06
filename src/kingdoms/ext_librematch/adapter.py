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
from kingdoms.core.models.game import GameMap, MatchDetails, PlayerStats, Slot, StatsBlock, StatsEntry
from kingdoms.core.rpc.rate_limit import ProviderRateLimiter

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://aoe-api.worldsedgelink.com"
LOBBIES_PATH = "/community/advertisement/findAdvertisements"
LEADERBOARDS_PATH = "/api/leaderboard"
LOBBIES_QUERY_PARAMS = {"title": "age2"}
LEADERBOARD_KEYS = {"0": "rm_1v1", "1": "rm_team", "2": "unranked", "3": "dm_1v1", "4": "dm_team"}

_SLOT_KINDS = {True: "human", False: "open"}


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
        self._base_url = base_url.rstrip("/")
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
        """Fetch a profile's leaderboard stats as pre-formatted blocks.

        Uses the authenticated leaderboard endpoint when an API key is
        configured; without one, degrades to None (the caller falls back
        to the manual path). Transport/decode failures also degrade —
        the provider never fails the serving process.
        """
        if not self._api_key or not profile_id:
            return None
        if not await self._allowed():
            logger.warning("librematch call over rate budget; degrading stats to None")
            return None
        blocks: list[StatsBlock] = []
        async with httpx.AsyncClient(timeout=self._timeout_s, transport=self._transport) as client:
            for board_id, board_name in LEADERBOARD_KEYS.items():
                try:
                    reply = await client.get(
                        urljoin(self._base_url + "/", LEADERBOARDS_PATH.lstrip("/")),
                        params={"leaderboardId": board_id, "profileIds": f'["{profile_id}"]'},
                        headers={"Ocp-Apim-Subscription-Key": self._api_key},
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

    def _parse_leaderboard_entry(self, payload: dict[str, Any], profile_id: str) -> list[StatsEntry]:
        """Extract one profile's stats entries from a leaderboard reply."""
        for item in payload.get("result", payload.get("leaderboard", [])):
            if not isinstance(item, dict):
                continue
            if str(item.get("profileId", item.get("profile_id", ""))) != str(profile_id):
                continue
            return [
                StatsEntry(key="rank", value=str(item.get("rank", ""))),
                StatsEntry(key="rating", value=str(item.get("rating", item.get("elo", "")))),
                StatsEntry(key="wins", value=str(item.get("wins", ""))),
                StatsEntry(key="losses", value=str(item.get("losses", ""))),
                StatsEntry(key="streak", value=str(item.get("streak", ""))),
                StatsEntry(key="games", value=str(item.get("games", ""))),
            ]
        return []

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
                    profile_id=str(
                        entry.get("profile_id", entry.get("playerId", entry.get("profileInfo.id", "")))
                    ),
                    faction_key=str(civ),
                    team=int(str(entry.get("team", entry.get("teamID", 0)) or "0")),
                    filled=filled,
                    slot_kind=str(entry.get("slot_kind", "")) or _SLOT_KINDS[filled],
                )
            )
        return slots
