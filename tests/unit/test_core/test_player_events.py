"""Unit tests for the generic player status events mechanism.

Game- and provider-agnostic dispatch: a transition reaches exactly the
mods where the player is enrolled, and one mod's failure never blocks
the others.
"""

from __future__ import annotations

from kingdoms.core.services.player_events import (
    PlayerStatusEvent,
    PlayerStatusRouter,
)


def _event(user_id: str = "10", new_status: str = "in_game") -> PlayerStatusEvent:
    return PlayerStatusEvent(
        game_key="aoe2",
        profile_id="123",
        user_id=user_id,
        old_status="in_lobby",
        new_status=new_status,
        match_ref="m1",
        match_url="https://www.aoe2lobby.com/w/m1",
        occurred_at=1_000,
    )


class _Membership:
    """Configurable enrollment seam."""

    def __init__(self, enrolled: set[str]) -> None:
        self._enrolled = enrolled
        self.lookups = 0

    async def is_enrolled(self, game_key: str, user_id: str) -> bool:
        self.lookups += 1
        return user_id in self._enrolled


class _Sink:
    """Recording notification sink."""

    def __init__(self) -> None:
        self.events: list[PlayerStatusEvent] = []

    async def on_player_status(self, event: PlayerStatusEvent) -> None:
        self.events.append(event)


class _BrokenSink:
    """A sink that always raises — must not block the other mods."""

    async def on_player_status(self, event: PlayerStatusEvent) -> None:
        raise RuntimeError("boom")


async def test_dispatch_notifies_only_enrolled_mods() -> None:
    router = PlayerStatusRouter()
    sink_a, sink_b = _Sink(), _Sink()
    router.register_mod("ladder", _Membership({"10"}), sink_a)
    router.register_mod("kingdoms", _Membership({"20"}), sink_b)
    notified = await router.dispatch(_event())
    assert notified == ["ladder"]
    assert len(sink_a.events) == 1
    assert sink_b.events == []


async def test_dispatch_survives_a_broken_sink() -> None:
    router = PlayerStatusRouter()
    sink = _Sink()
    router.register_mod("broken", _Membership({"10"}), _BrokenSink())
    router.register_mod("ladder", _Membership({"10"}), sink)
    notified = await router.dispatch(_event())
    assert notified == ["ladder"]
    assert len(sink.events) == 1


async def test_dispatch_without_registered_mods_is_noop() -> None:
    router = PlayerStatusRouter()
    assert await router.dispatch(_event()) == []


async def test_unregister_mod_stops_notifications() -> None:
    router = PlayerStatusRouter()
    sink = _Sink()
    router.register_mod("ladder", _Membership({"10"}), sink)
    await router.dispatch(_event())
    router.unregister_mod("ladder")
    assert await router.dispatch(_event()) == []
    assert len(sink.events) == 1
