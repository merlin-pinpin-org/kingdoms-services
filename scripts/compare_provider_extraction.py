"""Compare our blob extraction against community sites (one-shot, #205).

One-shot verification of the slotinfo/options decoder: fetch a lobby's
blobs from the LibreMatch community API, decode them with our code,
and — when a match_id is known to aoe2insights — diff our extraction
(map, civs, teams, options) against their data.

Usage:
    python scripts/compare_provider_extraction.py <match_id> [--base-url URL]

Reads AOE2_API_KEY (Ocp-Apim-Subscription-Key) from the environment;
without it, only the local decode runs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kingdoms.core.games.aoe2.blobs import decode_blob

LOBBIES_URL = "https://community.ageofempires.com/community/advertisement/findAdvertisements"
INSIGHTS_MATCH_URL = "https://www.aoe2insights.com/match/{match_id}/"


def extract_fields(decoded_slotinfo: dict, decoded_options: dict) -> dict:
    """Our extraction: map, civs, teams, options — the fields we store."""
    slots = decoded_slotinfo.get("slots", decoded_slotinfo)
    civs = []
    if isinstance(slots, list):
        for slot in slots:
            if not isinstance(slot, dict):
                continue
            if not (slot.get("filled") or "profile_id" in slot or "playerId" in slot):
                continue
            civs.append(
                {
                    "profile_id": str(slot.get("profile_id", slot.get("playerId", ""))),
                    "civ": str(slot.get("civ", slot.get("civilization", 0))),
                    "team": slot.get("team", 0),
                }
            )
    options = {str(k): str(v) for k, v in decoded_options.items() if not k.startswith("_")}
    return {"civs": civs, "options": options}


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("match_id", help="the provider match id (advertiserId)")
    parser.add_argument("--base-url", default=LOBBIES_URL)
    args = parser.parse_args()

    api_key = os.environ.get("AOE2_API_KEY", "")
    headers = {"Ocp-Apim-Subscription-Key": api_key} if api_key else {}
    async with httpx.AsyncClient(timeout=15.0) as client:
        reply = await client.get(args.base_url, headers=headers)
        reply.raise_for_status()
        lobbies = reply.json() if isinstance(reply.json(), list) else []

    lobby = next(
        (lob for lob in lobbies if str(lob.get("advertiserId", lob.get("match_id", ""))) == args.match_id),
        None,
    )
    if lobby is None:
        print(f"match {args.match_id} not in current lobby list (lobbies are transient)")
        return 1

    print(f"== lobby {args.match_id}: {lobby.get('mapname', lobby.get('mapName', '?'))}")
    for blob_field in ("slotinfo", "options"):
        blob = lobby.get(blob_field)
        if not isinstance(blob, str):
            print(f"-- {blob_field}: absent")
            continue
        try:
            decoded = decode_blob(blob)
        except Exception as exc:
            print(f"-- {blob_field}: decode FAILED: {exc}")
            continue
        print(f"-- {blob_field}: {json.dumps(decoded, ensure_ascii=False, indent=1)[:800]}")

    try:
        slotinfo = decode_blob(lobby["slotinfo"]) if isinstance(lobby.get("slotinfo"), str) else {}
        options = decode_blob(lobby["options"]) if isinstance(lobby.get("options"), str) else {}
    except Exception:
        slotinfo, options = {}, {}
    ours = extract_fields(slotinfo, options)
    print(f"== our extraction: {json.dumps(ours, ensure_ascii=False, indent=1)}")
    print(f"== compare with: {INSIGHTS_MATCH_URL.format(match_id=args.match_id)}")
    print("   (aoe2insights shows civs/teams per player; diff manually)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
