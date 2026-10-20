"""Kingdoms guide of the Règles forum — unit tests (guide-regles-v1).

The guide deployer must create every thread on a fresh forum, edit the
existing threads in place (rename + starter edit, never a duplicate),
match a renamed thread through its marker, and never raise when the
forum is missing. Every value shown to the players rides the live
config — the test asserts the real defaults land in the content.

restructuration-serveur-v1 d (Drasah 19/10): the forum itself is
retired (the guide lives in the Scriptorium « 📖 Règles » button) but
``guide_posts``/``refresh_guide_forum`` stay tolerant — the posts tests
pin the guide DATA the button now serves, and the console command now
answers a fixed pointer instead of rebuilding threads.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kingdoms.mods.kingdoms.admin_console import COMMANDES, AdminConsole
from kingdoms.mods.kingdoms.config import default_season_config
from kingdoms.mods.kingdoms.kingdom_guide import (
    GUIDE_MARKER_PREFIX,
    guide_posts,
    refresh_guide_forum,
)

pytestmark = pytest.mark.asyncio


class _StarterMessage:
    def __init__(self, content: str) -> None:
        self.content = content
        self.edits = 0

    async def edit(self, content: str | None = None, **kwargs: object) -> None:
        if content is not None:
            self.content = content
        self.edits += 1


class _Thread:
    def __init__(self, name: str, content: str) -> None:
        self.name = name
        self.starter_message = _StarterMessage(content)
        self.renames = 0

    async def edit(self, name: str | None = None, **kwargs: object) -> None:
        if name is not None:
            self.name = name
            self.renames += 1

    async def get_starter_message(self) -> _StarterMessage:
        return self.starter_message


class _Forum:
    def __init__(self, name: str = "Règles") -> None:
        self.name = name
        self.threads: list[_Thread] = []
        self.created: list[tuple[str, str]] = []

    async def create_thread(self, name: str, content: str) -> _Thread:
        self.created.append((name, content))
        thread = _Thread(name, content)
        self.threads.append(thread)
        return thread


class _Guild:
    def __init__(self, forums: list[_Forum]) -> None:
        self.forums = forums
        self.text_channels: list[object] = []


class _Service:
    config = default_season_config()


class _NoConfigService:
    config = None


# ----------------------------------------------------------------------
# The posts themselves
# ----------------------------------------------------------------------


def test_guide_posts_carry_live_values_and_markers() -> None:
    posts = guide_posts(default_season_config())
    assert len(posts) == 10
    keys = [key for key, _title, _content in posts]
    assert len(set(keys)) == 10
    for key, title, content in posts:
        assert len(title) <= 100
        assert len(content) <= 2000, key
        assert content.rstrip().endswith(f"-# {GUIDE_MARKER_PREFIX}{key}")
        assert content != ""
    bodies = dict(zip(keys, (c for _k, _t, c in posts), strict=True))
    assert "5 territoires" in bodies["royaume"]
    assert "8 territoires" in bodies["bienvenue"]
    assert "6 h" in bodies["attaques"]  # player_attack_delay_minutes=360
    assert "T-15" in bodies["attaques"]
    assert "23 h 30" in bodies["rythme"]  # cycle_cron 30 23 * * SUN
    assert "dimanche" in bodies["rythme"]
    assert "52 h" in bodies["beta"]
    assert "Embuscade" in bodies["tresor"]


def test_guide_posts_follow_a_config_override() -> None:
    config = default_season_config()
    config.attacks.attacks_per_week = 3
    config.territories_per_kingdom = 7
    bodies = dict(zip((k for k, _t, _c in guide_posts(config)), (c for _k, _t, c in guide_posts(config)), strict=True))
    assert "3 attaque(s)" in bodies["attaques"]
    assert "7 territoires" in bodies["royaume"]


# ----------------------------------------------------------------------
# The forum deployer
# ----------------------------------------------------------------------


async def test_refresh_creates_every_thread_on_a_fresh_forum() -> None:
    forum = _Forum()
    guild = _Guild([forum])
    report = await refresh_guide_forum(guild, _Service())
    assert len(report) == 10
    assert set(report.values()) == {"created"}
    assert len(forum.threads) == 10
    assert len(forum.created) == 10
    titles = [name for name, _content in forum.created]
    assert any("Bienvenue" in title for title in titles)


async def test_refresh_edits_existing_threads_in_place() -> None:
    forum = _Forum()
    guild = _Guild([forum])
    await refresh_guide_forum(guild, _Service())
    threads_before = list(forum.threads)
    report = await refresh_guide_forum(guild, _Service())
    assert set(report.values()) == {"updated"}
    assert forum.threads == threads_before
    assert len(forum.created) == 10  # no duplicate creation
    assert all(thread.starter_message.edits == 1 for thread in forum.threads)


async def test_refresh_matches_a_renamed_thread_by_its_marker() -> None:
    forum = _Forum()
    guild = _Guild([forum])
    await refresh_guide_forum(guild, _Service())
    posts = guide_posts(_Service().config)
    target = next(t for t in forum.threads if t.name == posts[0][1])
    await target.edit(name="ancien titre")
    report = await refresh_guide_forum(guild, _Service())
    assert report[posts[0][0]] == "updated"
    assert target.name == posts[0][1]  # renamed back
    assert len(forum.threads) == 10


async def test_refresh_skips_a_missing_forum_without_raising() -> None:
    guild = _Guild([])
    assert await refresh_guide_forum(guild, _Service()) == {}
    assert await refresh_guide_forum(guild, _NoConfigService()) == {}


async def test_refresh_reports_a_failed_thread_best_effort() -> None:
    class _BrokenForum(_Forum):
        async def create_thread(self, name: str, content: str) -> _Thread:
            raise RuntimeError("boom")

    guild = _Guild([_BrokenForum()])
    report = await refresh_guide_forum(guild, _Service())
    assert len(report) == 10
    assert set(report.values()) == {"failed"}


# ----------------------------------------------------------------------
# The console command
# ----------------------------------------------------------------------


async def test_regles_command_points_to_the_scriptorium_button() -> None:
    # restructuration-serveur-v1 d (Drasah 19/10): the Règles forum is
    # gone — the command answers a fixed pointer, nothing to rebuild.
    bot = SimpleNamespace(kingdoms_service=_Service())
    console = AdminConsole(bot)
    console._current_message = SimpleNamespace(guild=_Guild([]))
    reply = await COMMANDES["regles"].handler(console, [])
    assert "📖 Règles" in reply
    assert "Scriptorium" in reply
    assert "live" in reply
    # The answer is guild/wiring independent (no rebuild, no lookup).
    console._current_message = SimpleNamespace(guild=None)  # type: ignore[assignment]
    reply = await COMMANDES["regles"].handler(console, [])
    assert "Scriptorium" in reply
