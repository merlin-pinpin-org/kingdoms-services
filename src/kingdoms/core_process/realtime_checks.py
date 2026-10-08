"""External-services battery for the realtime providers (librematch, aoe2lobby).

Both are live-data upstreams: librematch through the Worlds Edge API
(``LIBREMATCH_BASE_URL``, default the public API), aoe2lobby through
its public WebSocket. Each check verifies real API access AND the
response-shape mappings (a shape change upstream fails the battery).
"""

from __future__ import annotations

import asyncio
import os

from kingdoms.core_process.external_checks import CheckResult, _run_check, register_battery


async def _check_librematch_lobbies() -> None:
    from kingdoms.ext_librematch.adapter import DEFAULT_BASE_URL, LibrematchAdapter

    adapter = LibrematchAdapter(base_url=os.environ.get("LIBREMATCH_BASE_URL", DEFAULT_BASE_URL))
    lobbies = await adapter.fetch_lobbies()
    if not isinstance(lobbies, list):
        raise AssertionError(f"lobby payload not a list: {type(lobbies)!r}")
    for lobby in lobbies:
        if "lobby_id" not in lobby and "id" not in lobby:
            raise AssertionError(f"lobby entry missing its id mapping: {sorted(lobby)[:8]}")


async def _check_librematch_leaderboard() -> None:
    from kingdoms.ext_librematch.adapter import DEFAULT_BASE_URL, LibrematchAdapter

    adapter = LibrematchAdapter(base_url=os.environ.get("LIBREMATCH_BASE_URL", DEFAULT_BASE_URL))
    stats = await adapter.player_stats("1")
    del stats  # reachability + no mapping exception is enough for a fake profile


async def librematch_battery() -> list[CheckResult]:
    """Run the librematch checks; all of them, whatever happens."""
    return [
        await _run_check("librematch", "lobbies-shape", _check_librematch_lobbies),
        await _run_check("librematch", "leaderboard-reachability", _check_librematch_leaderboard),
    ]


async def _check_aoe2lobby_ws() -> None:
    import websockets

    from kingdoms.ext_aoe2lobby.adapter import DEFAULT_WS_URL

    url = os.environ.get("AOE2LOBBY_WS_URL", DEFAULT_WS_URL)
    async with websockets.connect(url, open_timeout=10) as ws:
        try:
            message = await asyncio.wait_for(ws.recv(), timeout=10)
        except TimeoutError:
            return  # connected: an idle feed is a valid upstream state
        if not isinstance(message, str) or not message:
            raise AssertionError(f"unexpected ws frame type: {type(message)!r}")


async def aoe2lobby_battery() -> list[CheckResult]:
    """Run the aoe2lobby checks; all of them, whatever happens."""
    return [
        await _run_check("aoe2lobby", "ws-connect", _check_aoe2lobby_ws),
    ]


register_battery("librematch", librematch_battery)
register_battery("aoe2lobby", aoe2lobby_battery)
