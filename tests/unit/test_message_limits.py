"""Unit tests for the Discord message-size safety seam."""

from __future__ import annotations

import pytest

from kingdoms.discord.message_limits import (
    MAX_EMBED_DESCRIPTION,
    MAX_MESSAGE,
    MultiMessageRegistry,
    paginate,
    truncate_body,
)


class TestTruncateBody:
    def test_short_body_untouched(self) -> None:
        assert truncate_body("short") == "short"

    def test_exact_limit_untouched(self) -> None:
        body = "a" * MAX_MESSAGE
        assert truncate_body(body) == body

    def test_long_body_truncated_with_marker(self) -> None:
        lines = [f"line {i} padding padding padding" for i in range(200)]
        body = "\n".join(lines)
        result = truncate_body(body)
        assert len(result) <= MAX_MESSAGE
        assert "(+N more)" or "more)" in result
        assert result.count("padding") < 200

    def test_marker_reports_hidden_lines(self) -> None:
        lines = ["x" * 10] * 500
        body = "\n".join(lines)
        result = truncate_body(body)
        assert len(result) <= MAX_MESSAGE
        assert "(+" in result and "more)" in result

    def test_embed_limit(self) -> None:
        body = "y" * 6000
        result = truncate_body(body, MAX_EMBED_DESCRIPTION)
        assert len(result) <= MAX_EMBED_DESCRIPTION


class TestPaginate:
    def test_short_body_single_page(self) -> None:
        assert paginate("short") == ["short"]

    def test_pages_within_limit(self) -> None:
        lines = [f"entry {i} " + "z" * 50 for i in range(400)]
        pages = paginate("\n".join(lines))
        assert len(pages) > 1
        assert all(len(p) <= MAX_MESSAGE for p in pages)

    def test_line_boundaries_preserved(self) -> None:
        lines = [f"line-{i}" for i in range(600)]
        pages = paginate("\n".join(lines), limit=100)
        assert all(len(p) <= 100 for p in pages)
        joined = [ln for p in pages for ln in p.split("\n")]
        assert joined == lines

    def test_pathological_single_line(self) -> None:
        pages = paginate("q" * 5000, limit=200)
        assert all(len(p) <= 200 for p in pages)


class _FakeMessage:
    def __init__(self, id: int, content: str) -> None:
        self.id = id
        self.content = content
        self.channel_id = "42"
        self.edited = 0
        self.deleted = False

    async def edit(self, content: str, **kwargs: object) -> None:
        self.edited += 1
        self.content = content

    async def delete(self) -> None:
        self.deleted = True


class _FakeChannel:
    def __init__(self) -> None:
        self.messages: dict[int, _FakeMessage] = {}
        self._next = 100
        self.fetched: list[int] = []

    async def send(self, content: str, **kwargs: object) -> _FakeMessage:
        msg = _FakeMessage(self._next, content)
        self.messages[self._next] = msg
        self._next += 1
        return msg

    async def fetch_message(self, mid: int) -> _FakeMessage:
        self.fetched.append(mid)
        return self.messages[mid]


class _FakeRegistry:
    def __init__(self) -> None:
        self.store: dict[str, object] = {}

    async def resolve(self, platform: str, key: str, entity_id: str) -> object | None:
        return self.store.get(entity_id)

    async def register(
        self,
        platform: str,
        message_key: str,
        entity_id: str,
        channel_id: str,
        message_id: str,
    ) -> None:
        self.store[entity_id] = _Registered(channel_id, message_id)


class _Registered:
    def __init__(self, channel_id: str, message_id: str) -> None:
        self.channel_id = channel_id
        self.message_id = message_id


@pytest.mark.asyncio
async def test_registry_first_publish() -> None:
    channel = _FakeChannel()
    registry = _FakeRegistry()
    multi = MultiMessageRegistry(registry, "discord", "live")
    ids = await multi.publish(channel, "e1", "hello")
    assert ids == ["100"]
    assert registry.store["e1"].message_id == "100"


@pytest.mark.asyncio
async def test_registry_edit_in_place() -> None:
    channel = _FakeChannel()
    registry = _FakeRegistry()
    multi = MultiMessageRegistry(registry, "discord", "live")
    await multi.publish(channel, "e1", "hello")
    await multi.publish(channel, "e1", "hello world")
    assert len(channel.messages) == 1
    assert channel.messages[100].content == "hello world"
    assert channel.messages[100].edited == 1


@pytest.mark.asyncio
async def test_registry_grow_appends() -> None:
    channel = _FakeChannel()
    registry = _FakeRegistry()
    multi = MultiMessageRegistry(registry, "discord", "live")
    await multi.publish(channel, "e1", "one")
    big = "\n".join(f"line {i} " + "x" * 100 for i in range(60))
    await multi.publish(channel, "e1", big)
    pages = [m.content for m in channel.messages.values()]
    assert len(pages) > 1
    assert registry.store["e1"].message_id.count(",") == len(pages) - 1


@pytest.mark.asyncio
async def test_registry_shrink_deletes() -> None:
    channel = _FakeChannel()
    registry = _FakeRegistry()
    multi = MultiMessageRegistry(registry, "discord", "live", limit=200)
    big = "\n".join(f"line {i} " + "x" * 50 for i in range(40))
    await multi.publish(channel, "e1", big)
    assert len(channel.messages) > 1
    await multi.publish(channel, "e1", "small")
    live = [m for m in channel.messages.values() if not m.deleted]
    assert len(live) == 1
    assert live[0].content == "small"


@pytest.mark.asyncio
async def test_registry_stale_ids_republish() -> None:
    channel = _FakeChannel()
    registry = _FakeRegistry()
    multi = MultiMessageRegistry(registry, "discord", "live")

    class _VanishingChannel(_FakeChannel):
        async def fetch_message(self, mid: int) -> _FakeMessage:
            raise RuntimeError("404")

    multi = MultiMessageRegistry(registry, "discord", "live")
    await multi.publish(channel, "e1", "first")
    multi_vanish = MultiMessageRegistry(registry, "discord", "live")
    # simulate all registered ids being stale
    registry.store["e1"].message_id = "999,1000"
    gone = _VanishingChannel()
    ids = await multi_vanish.publish(gone, "e1", "fresh")
    assert ids == ["100"]
    assert gone.messages[100].content == "fresh"
