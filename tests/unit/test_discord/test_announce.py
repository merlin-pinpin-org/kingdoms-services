"""Unit tests for the startup announcement (kingdoms-services#52, #109).

The announcement is the "start" lifecycle event, delivered by the core
LogService to each guild's 🤖-bot-logs channel. These tests pin the
frozen footer format, the Components V2 layout contract (identity on
the buttons, timestamp under each artifact, Services/Infra/Bot
sections) and the wiring contracts: with a LogService the event flows
to every guild; without one (local runs, unit tests) the announcement
degrades to a silent skip. KINGDOMS_ANNOUNCE_ENABLED=0 silences it
entirely (CI/CD bot).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import discord
import pytest

from kingdoms.core.services.logs import LifecycleEvent, LogService
from kingdoms.core.services.mod_registry import ModRegistry
from kingdoms.core.services.status import BotAdmins, StatusService
from kingdoms.discord.announce import (
    AnnounceConfig,
    announce_startup,
    build_announcement_layout,
    deploy_footer,
)

CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"


def _status_service(**kwargs: str) -> StatusService:
    return StatusService(registry=ModRegistry({}), bot_admins=BotAdmins(), **kwargs)


class _FakeLogService:
    """LogService stand-in capturing the delivered events per guild."""

    def __init__(self) -> None:
        self.events: dict[str, list[LifecycleEvent]] = {}

    async def log_event(self, guild_id: str, event: LifecycleEvent) -> None:
        self.events.setdefault(guild_id, []).append(event)


class _Bot:
    """Client stand-in exposing the guilds and the gateway latency."""

    def __init__(self, guild_ids: list[str], latency: float | None = None) -> None:
        self.guilds = [type("G", (), {"id": int(gid)})() for gid in guild_ids]
        self.latency = latency


TYPE_TEXT_DISPLAY = 10
TYPE_SEPARATOR = 14
TYPE_CONTAINER = 17
TYPE_ACTION_ROW = 1
TYPE_BUTTON = 2
TYPE_SECTION = 9


def _walk(components: Any) -> list[dict[str, Any]]:
    """Depth-first walk of a wire component tree (children + accessories)."""
    out: list[dict[str, Any]] = []
    for component in components:
        out.append(component)
        out.extend(_walk(component.get("components", [])))
        accessory = component.get("accessory")
        if accessory:
            out.append(accessory)
            out.extend(_walk(accessory.get("components", [])))
    return out


def _iter_texts(components: Any) -> list[str]:
    """Flatten every TextDisplay content of a wire V2 component tree."""
    return [c["content"] for c in _walk(components) if c.get("type") == TYPE_TEXT_DISPLAY]


def _iter_buttons(components: Any) -> list[dict[str, Any]]:
    """Flatten every button of a wire V2 component tree."""
    return [c for c in _walk(components) if c.get("type") == TYPE_BUTTON]


def test_footer_format_is_frozen() -> None:
    """The footer only repeats the environment — the identity (image,
    kind, ref, run) already rides in the body buttons; the battery
    (kingdoms-infra#78) reads the pinned state from the state file."""
    status = _status_service(
        deploy_label="pr-42-20260925-abc1234",
        deploy_kind="pr",
        deploy_ref="42",
        deploy_run_number="123",
    )
    assert deploy_footer(status, env="test") == "kingdoms-deploy test"


def test_footer_renders_empty_env() -> None:
    status = _status_service()
    assert deploy_footer(status, env="") == "kingdoms-deploy"


def test_layout_is_components_v2() -> None:
    status = _status_service(
        deploy_label="pr-42-x",
        deploy_kind="pr",
        deploy_ref="42",
        deploy_url="https://github.com/merlin-pinpin-org/kingdoms-services/pull/42#issuecomment-1",
        deploy_image="ghcr.io/merlin-pinpin-org/kingdoms-services:pr-42-x",
    )
    config = AnnounceConfig(locale="en", config_dir=CONFIG_DIR)
    layout = build_announcement_layout(status, config, env="test")
    assert isinstance(layout, discord.ui.LayoutView)
    assert layout.to_components(), "the layout must serialize to V2 components"
    texts = _iter_texts(layout.to_components())
    joined = "\n".join(texts)
    assert "Kingdoms — Deployment" in joined
    assert "`test`" in joined
    assert not any("[" in text and "](" in text for text in texts), "no markdown links in V2 text blocks"
    assert deploy_footer(status, env="test") in joined


def test_layout_buttons_carry_the_identity() -> None:
    """Buttons replace labels with the artifact identity + emoji."""
    status = _status_service(
        deploy_branch="vibe/feature-1",
        deploy_kind="pr",
        deploy_ref="42",
        deploy_url="https://github.com/merlin-pinpin-org/kingdoms-services/pull/42",
        deploy_tree_url="https://github.com/merlin-pinpin-org/kingdoms-services/tree/abc1234deadbeef",
        deploy_image="ghcr.io/merlin-pinpin-org/kingdoms-services:pr-42-20260925-abc1234",
        deploy_run_url="https://github.com/merlin-pinpin-org/kingdoms-infra/actions/runs/1",
        deploy_run_number="45",
        deploy_infra_label="deploy/test@c232b34deadbeef",
        deploy_infra_url="https://github.com/merlin-pinpin-org/kingdoms-infra/tree/c232b34deadbeef",
    )
    config = AnnounceConfig(locale="en", config_dir=CONFIG_DIR)
    layout = build_announcement_layout(status, config, env="test")
    labels = [b["label"] for b in _iter_buttons(layout.to_components())]
    assert "🌿 vibe/feature-1" in labels, "branch button carries the branch name"
    assert "🔧 abc1234" in labels, "commit button carries the sha7"
    assert "🌿 deploy/test" in labels, "infra branch button carries the state branch"
    assert "🔧 c232b34" in labels, "infra commit button carries the state sha7"
    assert any(label.startswith("🚀 #") for label in labels), "deployment button carries the run id"
    assert not any(label in {"Branch", "Commit", "Files", "Deployment"} for label in labels), (
        "no generic label buttons"
    )


def test_docker_tag_is_shortened() -> None:
    """The pinned docker tag is too long for a text line: it renders
    shortened (sha7 + short stamp), never the full pr-<id>-<stamp>-<sha>."""
    status = _status_service(
        deploy_image="ghcr.io/merlin-pinpin-org/kingdoms-services:pr-42-20260925222854-abc1234",
    )
    config = AnnounceConfig(locale="en", config_dir=CONFIG_DIR)
    layout = build_announcement_layout(status, config, env="test")
    texts = "\n".join(_iter_texts(layout.to_components()))
    assert "pr-42-20260925222854-abc1234" not in texts, "the full tag never renders"
    assert "abc1234" in texts, "the sha7 of the tag renders"


def test_timestamps_sit_under_commit_and_build_for_both_repos() -> None:
    """Services: commit date under the commit line, build date under the
    image line. Infra: commit date under the state commit line, run
    date under the deployment line."""
    status = _status_service(
        deploy_branch="vibe/feature-1",
        deploy_tree_url="https://github.com/merlin-pinpin-org/kingdoms-services/tree/abc1234deadbeef",
        deploy_commit_ts="1790000000",
        deploy_ts="1790100000",
        deploy_image="ghcr.io/merlin-pinpin-org/kingdoms-services:pr-42-x",
        deploy_infra_label="deploy/test@abc1234deadbeef",
        deploy_infra_commit_ts="1790050000",
        deploy_run_url="https://github.com/merlin-pinpin-org/kingdoms-infra/actions/runs/1",
        deploy_run_number="45",
        deploy_run_ts="1790150000",
    )
    config = AnnounceConfig(locale="en", config_dir=CONFIG_DIR)
    layout = build_announcement_layout(status, config, env="test")
    texts = _iter_texts(layout.to_components())
    services = next(t for t in texts if "Branch" in t and "abc1234" in t and "2026" not in t)
    assert "🔧 Commit `abc1234` <t:1790000000:R>" in services.splitlines()
    image_line = next(t for t in texts if "📦" in t and "<t:1790100000:R>" in t)
    assert image_line
    infra = next(t for t in texts if "deploy/test" in t)
    assert "🔧 Commit `abc1234` <t:1790050000:R>" in infra.splitlines()
    deploy_line = next(t for t in texts if "🚀" in t and "<t:1790150000:R>" in t)
    assert deploy_line


def test_layout_bot_section_reports_uptime_admins_games_mods_latency() -> None:
    """The Bot section mirrors /status: uptime, admins as mentions,
    games, enabled mods, gateway latency."""

    def advancing_clock() -> float:
        advancing_clock.now += 3661.0
        return advancing_clock.now

    advancing_clock.now = 0.0
    status = StatusService(
        registry=ModRegistry({}),
        bot_admins=BotAdmins(user_ids=("111", "222")),
        games=("werewolf", "alliance"),
        clock=advancing_clock,
    )
    config = AnnounceConfig(locale="en", config_dir=CONFIG_DIR)
    layout = build_announcement_layout(status, config, env="test", latency_ms=42)
    texts = "\n".join(_iter_texts(layout.to_components()))
    assert "**Bot**" in texts
    assert "- Uptime: 1h 1m 1s" in texts
    assert "<@111>" in texts and "<@222>" in texts
    assert "Games: werewolf, alliance" in texts
    assert "Latency: 42 ms" in texts


def test_layout_bot_section_without_admins_or_games() -> None:
    status = _status_service()
    config = AnnounceConfig(locale="en", config_dir=CONFIG_DIR)
    layout = build_announcement_layout(status, config)
    texts = "\n".join(_iter_texts(layout.to_components()))
    assert "Admins" not in texts, "no admins line when no operator is configured"
    assert "*(none configured)*" in texts


def test_layout_sections_and_separator_structure() -> None:
    """A release deploy: Version vX.Y.Z headlines as a Section; the Bot
    section (uptime, admins, games) sits between Infra and the footer."""
    status = _status_service(
        deploy_label="v0.1.0",
        deploy_kind="release",
        deploy_ref="v0.1.0",
        deploy_url="https://github.com/merlin-pinpin-org/kingdoms-services/releases/tag/v0.1.0",
    )
    config = AnnounceConfig(locale="en", config_dir=CONFIG_DIR)
    layout = build_announcement_layout(status, config, env="prod")
    top = layout.to_components()
    assert len(top) == 1 and top[0]["type"] == TYPE_CONTAINER
    kinds = [c["type"] for c in top[0]["components"]]
    assert kinds.count(TYPE_TEXT_DISPLAY) >= 2
    assert kinds.count(TYPE_SEPARATOR) == 3, "Services/Infra, Infra/Bot, Bot/footer"
    assert kinds.count(TYPE_SECTION) == 1, "the release headline is a Section"
    texts = _iter_texts(top)
    assert any("Version v0.1.0" in text for text in texts), "releases headline as Version vX.Y.Z"
    assert any(text.startswith("**Bot**") for text in texts), "the Bot section is present"
    section = next(c for c in top[0]["components"] if c["type"] == TYPE_SECTION)
    assert section["accessory"]["label"] == "🔗 v0.1.0"


def test_layout_is_localized() -> None:
    status = _status_service(deploy_label="v0.1.0")
    config = AnnounceConfig(locale="fr", config_dir=CONFIG_DIR)
    layout = build_announcement_layout(status, config, env="prod")
    joined = "\n".join(_iter_texts(layout.to_components()))
    assert "Kingdoms — Déploiement" in joined


def test_layout_falls_back_to_english_for_unknown_locale() -> None:
    status = _status_service(deploy_label="sha-abc1234")
    config = AnnounceConfig(locale="xx", config_dir=CONFIG_DIR)
    layout = build_announcement_layout(status, config, env="test")
    joined = "\n".join(_iter_texts(layout.to_components()))
    assert "Kingdoms — Deployment" in joined


@pytest.mark.asyncio
async def test_announce_delivers_start_layout_to_every_guild() -> None:
    status = _status_service(deploy_label="pr-42-x")
    logs = _FakeLogService()
    bot = _Bot(["111", "222"], latency=0.045)
    config = AnnounceConfig(locale="en", config_dir=CONFIG_DIR)
    await announce_startup(bot, status, config, logs_service=logs, deploy_env="test")  # type: ignore[arg-type]
    assert set(logs.events) == {"111", "222"}
    for events in logs.events.values():
        assert len(events) == 1
        event = events[0]
        assert event.kind == "start"
        assert event.footer == "kingdoms-deploy test"
        assert isinstance(event.layout, discord.ui.LayoutView)
        joined = "\n".join(_iter_texts(event.layout.to_components()))
        assert "Latency: 45 ms" in joined, "the gateway latency rides in the Bot section"


@pytest.mark.asyncio
async def test_announce_disabled_silences_every_guild() -> None:
    status = _status_service(deploy_label="pr-42-x")
    logs = _FakeLogService()
    bot = _Bot(["111"])
    config = AnnounceConfig(locale="en", config_dir=CONFIG_DIR)
    await announce_startup(  # type: ignore[arg-type]
        bot, status, config, logs_service=logs, deploy_env="ci", enabled=False
    )
    assert logs.events == {}


@pytest.mark.asyncio
async def test_announce_skipped_without_log_service() -> None:
    status = _status_service()
    bot = _Bot(["111"])
    config = AnnounceConfig(locale="en", config_dir=CONFIG_DIR)
    await announce_startup(bot, status, config, logs_service=None, deploy_env="test")  # type: ignore[arg-type]


def test_log_service_protocol_shape() -> None:
    """The fake used in tests satisfies the LogService call surface used here."""
    assert isinstance(_FakeLogService().log_event, object)
    service: Any = _FakeLogService()
    assert callable(service.log_event)


def test_lifecycle_event_is_a_plain_dataclass() -> None:
    event = LifecycleEvent(kind="start", message="m", footer="f")
    assert event.kind == "start"
    assert event.footer == "f"
    assert event.layout is None
    assert LogService is not None
