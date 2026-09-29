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

from kingdoms.core.games.aoe2.blobs import BlobDecodeError, decode_blob
from kingdoms.core.models.game import GameMap, MatchDetails, Slot

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://community.ageofempires.com"
LOBBIES_PATH = "/community/advertisement/findAdvertisements"

_SLOT_KINDS = {True: "human", False: "open"}


class LibrematchAdapter:
    """HTTP client for the Worlds Edge Link Community API (lobbies)."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout_s: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        self._transport = transport

    async def fetch_lobbies(self) -> list[dict[str, Any]]:
        """Fetch the current public lobby listings (raw API payloads)."""
        async with httpx.AsyncClient(timeout=self._timeout_s, transport=self._transport) as client:
            reply = await client.get(urljoin(self._base_url + "/", LOBBIES_PATH.lstrip("/")))
            reply.raise_for_status()
            payload = reply.json()
        items = payload if isinstance(payload, list) else payload.get("result", [])
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
            except BlobDecodeError:
                logger.warning("undecodable %s blob for lobby %s", blob_field, lobby.get("match_id"))
                continue
            if target == "slots":
                slots_raw = self._parse_slots(decoded)
            else:
                options_raw = tuple(
                    (str(k), str(v)) for k, v in decoded.items() if not k.startswith("_")
                )
        return MatchDetails(
            match_ref=str(lobby.get("advertiserId", lobby.get("match_id", ""))),
            map_name=str(lobby.get("mapname", lobby.get("mapName", ""))),
            slots=tuple(slots_raw),
            options=options_raw,
            started_at=0,
            match_kind="lobby",
        )

    def _parse_slots(self, decoded: dict[str, object]) -> list[Slot]:
        """Parse the decoded slotinfo blob into per-slot models.

        The decoded structure varies across game versions; both a list
        of slot objects and a {"slots": [...]} wrapper are accepted.
        """
        entries = decoded.get("slots", decoded)
        if not isinstance(entries, list):
            return []
        slots: list[Slot] = []
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            civ = entry.get("civ", entry.get("civilization", 0))
            filled = bool(entry.get("filled", "profile_id" in entry or "playerId" in entry))
            slots.append(
                Slot(
                    slot_index=int(entry.get("slot_index", index)),
                    profile_id=str(entry.get("profile_id", entry.get("playerId", ""))),
                    faction_key=str(civ),
                    team=int(entry.get("team", 0)),
                    filled=filled,
                    slot_kind=str(entry.get("slot_kind", "")) or _SLOT_KINDS[filled],
                )
            )
        return slots
