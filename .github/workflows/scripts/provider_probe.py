"""Live provider probe: real calls to the third-party services, payload printed.

Debug tool for "the provider APIs seem down": runs the REAL adapter code
(``ext_librematch`` HTTP + ``ext_aoe2lobby`` WebSocket) against the REAL
services and prints the raw payloads, so we debug against facts.

Disabled by design: this script is only called by the ``provider-probe``
workflow input (manual dispatch, default off) — never by CI, never by
the bot, never in prod. One-shot, read-only, no database involved.

Usage:
    python scripts/provider_probe.py [--profile-id 848567] [--timeout 15]
                                      [--skip-ws] [--skip-http]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))


def _print(title: str, payload: object) -> None:
    """Print a section header and the payload, pretty and truncated-safe."""
    print(f"\n{'=' * 12} {title} {'=' * 12}")
    if isinstance(payload, (dict, list)):
        text = json.dumps(payload, indent=2, default=str)
        print(text[:4000] + ("\n... [truncated]" if len(text) > 4000 else ""))
    else:
        print(payload)


async def probe_librematch(profile_id: str, timeout_s: float) -> None:
    """HTTP probe: lobbies, one lobby's decoded match details, player stats."""
    from kingdoms.core.games.aoe2.blobs import BlobDecodeError, decode_blob
    from kingdoms.ext_librematch.adapter import LibrematchAdapter

    adapter = LibrematchAdapter(timeout_s=timeout_s)
    print(f"\n### ext_librematch — {adapter._base_url}")
    lobbies = await adapter.fetch_lobbies()
    _print(f"fetch_lobbies: {len(lobbies)} lobbies", lobbies[:3])
    if not lobbies:
        print("!! zero lobbies — API down, throttled, or empty")
        return
    first = lobbies[0]
    _print("first lobby raw keys", sorted(first.keys()))
    for blob_field in ("slotinfo", "options"):
        blob = first.get(blob_field) or (first.get("advertisement") or {}).get(blob_field)
        if blob:
            try:
                decoded = decode_blob(str(blob))
                _print(f"first lobby {blob_field} (decoded blob)", decoded)
            except (BlobDecodeError, ValueError) as err:
                print(f"!! {blob_field} decode failed: {err}")
    details = await adapter.match_details("probe")
    _print("match_details('probe') -> mapped from lobby", details)
    stats = await adapter.player_stats(profile_id)
    _print(f"player_stats({profile_id})", stats)


async def probe_aoe2lobby(timeout_s: float) -> None:
    """WebSocket probe: stream the first live events, then close."""
    import json as _json

    import websockets

    from kingdoms.ext_aoe2lobby.adapter import DEFAULT_WS_URL

    print(f"\n### ext_aoe2lobby — {DEFAULT_WS_URL}")
    try:
        async with websockets.connect(DEFAULT_WS_URL, open_timeout=timeout_s) as ws:
            print("connected — streaming up to 3 events (60s budget)")
            try:
                await ws.send(_json.dumps({"action": "subscribe", "feed": "lobbies"}))
                print("sent subscription message: subscribe/lobbies")
            except Exception as sub_err:
                print(f"(send failed: {sub_err})")
            for _ in range(3):
                raw = await asyncio.wait_for(ws.recv(), timeout=60)
                print(f"\n--- raw event ({len(str(raw))} bytes) ---")
                print(str(raw)[:2000] + ("\n... [truncated]" if len(str(raw)) > 2000 else ""))
                try:
                    payload = _json.loads(raw)
                    _print("event parsed", payload)
                except (ValueError, TypeError):
                    print("(not JSON)")
    except TimeoutError:
        print("!! no event within the budget — stream up but silent?")
    except Exception as err:
        print(f"!! websocket failed: {type(err).__name__}: {err}")


async def main_async(args: argparse.Namespace) -> int:
    """Run the enabled probes; exit 0 if at least one call succeeded."""
    ok = False
    if not args.skip_http:
        try:
            await probe_librematch(args.profile_id, args.timeout)
            ok = True
        except Exception as err:
            print(f"!! librematch probe failed: {type(err).__name__}: {err}")
    if not args.skip_ws:
        try:
            await probe_aoe2lobby(args.timeout)
            ok = ok or True
        except Exception as err:
            print(f"!! aoe2lobby probe failed: {type(err).__name__}: {err}")
    print(f"\nprobe done (at least one service answered: {ok})")
    return 0 if ok else 1


def main() -> int:
    """Parse args and run; exit code carries the verdict."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile-id", default="848567", help="profile_id for player_stats")
    parser.add_argument("--timeout", type=float, default=15.0, help="per-call timeout (s)")
    parser.add_argument("--skip-ws", action="store_true", help="skip the aoe2lobby WebSocket probe")
    parser.add_argument("--skip-http", action="store_true", help="skip the librematch HTTP probe")
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
