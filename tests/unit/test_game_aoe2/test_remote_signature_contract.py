"""The remote's signatures ARE the real service's signatures — every method.

The 2026-11-06 pool round: the remote's update_map_pool/create_map_pool
took **kwargs while every Discord caller passes positionals — 'takes 1
positional argument' in production. The tests stayed green because the
flow tests use a FAKE service and the remote tests call with kwargs:
the pieces were tested, the SEAM never was.

This contract compares the remote's public API against the REAL
service's, method by method: any drift — a kwarg-only remote behind a
positional caller, a renamed parameter, a missing method — fails HERE,
at commit time.
"""

from __future__ import annotations

import inspect


def _public_methods(cls: type) -> dict[str, inspect.Signature]:
    return {
        name: inspect.signature(func)
        for name, func in vars(cls).items()
        if not name.startswith("_") and inspect.isfunction(func)
    }


def test_the_remote_mirrors_the_real_service_signatures() -> None:
    """Every public method of the remote matches the real service's shape."""
    from botforge.core.services.game_data import GameDataService
    from botforge.game_aoe2.remote_catalog import RemoteGameDataService

    real = _public_methods(GameDataService)
    remote = _public_methods(RemoteGameDataService)

    # the pool lifecycle is the Discord layer's daily bread — every
    # stage must exist on the remote
    lifecycle = (
        "create_map_pool",
        "update_map_pool",
        "transition_map_pool",
        "archive_map_pool",
        "hard_delete_map_pool",
        "get_map_pool",
        "list_map_pools",
    )
    missing = sorted(set(lifecycle) - set(remote))
    assert not missing, f"the remote lacks the pool lifecycle: {missing}"

    for name, real_sig in real.items():
        if name not in remote:
            continue  # the remote is a purposeful subset; what it EXPOSES must match
        remote_sig = remote[name]
        real_params = list(real_sig.parameters.values())[1:]  # drop self
        remote_params = list(remote_sig.parameters.values())[1:]
        real_shape = [(p.name, p.kind) for p in real_params]
        remote_shape = [(p.name, p.kind) for p in remote_params]
        assert real_shape == remote_shape, (
            f"{name}: the remote's signature drifted from the real service — "
            f"real {real_shape} vs remote {remote_shape}. The Discord callers "
            "pass positionals; a kwarg-only remote crashes in production."
        )


# The remote's deliberate subset, made explicit (#323): the real
# service's methods the bot must NOT reach, each with its reason. This is
# a design decision, not drift — the assertion below fails the moment
# the real service grows a method the remote does not serve and nobody
# decided FORBIDDEN, and the moment a FORBIDDEN method leaks onto the
# remote or loses its real counterpart.
FORBIDDEN: dict[str, str] = {
    "activate_map_pool": (
        "the seasons seam (SeasonService.activate_season) — core-side only, "
        "a season's pool switch never rides the remote console"
    ),
    "seed_from_data": (
        "seeding runs in the game service at boot (game_aoe2/seed.py), never from the bot"
    ),
    "create_faction": (
        "faction entities are created by the game service's content sync "
        "(content_refresh.py); the bot's civ flows read content docs only"
    ),
    "get_faction": (
        "the civ index rides list_factions and the localized cards ride content_get; "
        "the raw faction entity stays game-service-side"
    ),
    "archive_faction": (
        "the bot never mutates the faction catalog — same owner rule as create_faction"
    ),
    "create_rule": (
        "rules are game-design data owned by the game service; no Discord flow touches them"
    ),
    "get_rule": "same owner rule as create_rule",
    "archive_rule": "same owner rule as create_rule",
}


def test_the_remote_subset_is_explicit() -> None:
    """Real methods are exactly: served by the remote OR forbidden here."""
    from botforge.core.services.game_data import GameDataService
    from botforge.game_aoe2.remote_catalog import RemoteGameDataService

    real = set(_public_methods(GameDataService))
    remote = set(_public_methods(RemoteGameDataService))

    silently_missing = sorted(real - remote - set(FORBIDDEN))
    assert not silently_missing, (
        "the real service has methods the remote does not serve and the "
        f"contract does not FORBID: {silently_missing} — decide per method: "
        "expose it on the remote (with the contract) or list it FORBIDDEN "
        "with the reason"
    )

    stale = sorted(set(FORBIDDEN) - real)
    assert not stale, f"FORBIDDEN entries with no real method behind them: {stale}"

    over_exposed = sorted(set(FORBIDDEN) & remote)
    assert not over_exposed, f"methods both exposed and FORBIDDEN: {over_exposed}"
