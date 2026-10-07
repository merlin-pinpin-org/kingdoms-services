"""Unit tests for the ladder pick strategies (#223)."""

from __future__ import annotations

import random

import pytest

from kingdoms.mods.ladder.pick_strategies import (
    PickContext,
    list_pick_strategies,
    register_pick_strategy,
    resolve_pick_strategy,
)


def _ctx(
    candidates: tuple[str, ...],
    host_favs: tuple[str, ...] = (),
    host_bans: tuple[str, ...] = (),
    guest_favs: tuple[str, ...] = (),
    guest_bans: tuple[str, ...] = (),
    seed: int = 0,
) -> PickContext:
    return PickContext(
        candidates=candidates,
        host_favs=host_favs,
        host_bans=host_bans,
        guest_favs=guest_favs,
        guest_bans=guest_bans,
        rng=random.Random(seed),  # noqa: S311 - test rng
    )


def test_three_modes_registered_by_default() -> None:
    keys = {s.key for s in list_pick_strategies()}
    assert {"random", "ban_filter", "weighted"} <= keys


def test_resolve_falls_back_to_weighted_on_unknown_key() -> None:
    assert resolve_pick_strategy("nope").key == "weighted"


def test_random_ignores_preferences() -> None:
    ctx = _ctx(("a", "b"), host_bans=("a",))
    assert resolve_pick_strategy("random").pick(ctx) in ("a", "b")


def test_ban_filter_excludes_bans() -> None:
    ctx = _ctx(("a", "b", "c"), host_bans=("a",), guest_bans=("b",))
    assert resolve_pick_strategy("ban_filter").pick(ctx) == "c"


def test_ban_filter_falls_back_when_all_banned() -> None:
    ctx = _ctx(("a",), host_bans=("a",))
    assert resolve_pick_strategy("ban_filter").pick(ctx) == "a"


def test_weighted_excludes_bans_and_weights_favs() -> None:
    ctx = _ctx(("a", "b"), host_favs=("b",), guest_bans=("a",))
    assert resolve_pick_strategy("weighted").pick(ctx) == "b"


def test_weighted_raises_when_all_banned() -> None:
    ctx = _ctx(("a",), host_bans=("a",))
    with pytest.raises(ValueError, match="banned"):
        resolve_pick_strategy("weighted").pick(ctx)


def test_register_is_idempotent_last_wins() -> None:
    class _Custom:
        key = "custom_test"
        label = "Custom"

        def pick(self, ctx: PickContext) -> str:
            """Return the first candidate."""
            return ctx.candidates[0]

    register_pick_strategy(_Custom())
    register_pick_strategy(_Custom())
    resolved = resolve_pick_strategy("custom_test")
    assert resolved.pick(_ctx(("x", "y"))) == "x"
