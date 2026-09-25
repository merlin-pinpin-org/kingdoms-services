"""Unit tests for the startup announcement (kingdoms-services#52, #109).

The announcement is the "start" lifecycle event, delivered by the core
LogService to each guild's 🤖-bot-logs channel. These tests pin the
frozen footer format, the Components V2 layout contract (same /status
rendering inside a Container/Section/Separator) and the wiring
contracts: with a LogService the event flows to every guild; without
one (local runs, unit tests) the announcement degrades to a silent
skip. KINGDOMS_ANNOUNCE_ENABLED=0 silences it entirely (CI/CD bot).
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
    """Client stand-in exposing the guilds the announcement iterates."""

    def __init__(self, guild_ids: list[str]) -> None:
        self.guilds = [type("G", (), {"id": int(gid)})() for gid in guild_ids]


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
    status = _status_service(
        deploy_label="pr-42-20260925-abc1234",
        deploy_kind="pr",
        deploy_ref="42",
        deploy_run_url="https://github.com/merlin-pinpin-org/kingdoms-infra/actions/runs/1",
    )
    footer = deploy_footer(status, env="test")
    assert footer == (
        "kingdoms-deploy env=test image=pr-42-20260925-abc1234 kind=pr ref=42 "
        "run=https://github.com/merlin-pinpin-org/kingdoms-infra/actions/runs/1"
    )


def test_footer_renders_empty_fields() -> None:
    status = _status_service()
    footer = deploy_footer(status, env="")
    assert footer == "kingdoms-deploy env= image= kind= ref= run="


def test_layout_is_components_v2_and_reuses_status_rendering() -> None:
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
    buttons = _iter_buttons(layout.to_components())
    labels = [b["label"] for b in buttons]
    assert "Pull-request" in labels and "Image" in labels
    assert not any("[" in text and "](" in text for text in texts), "no markdown links in V2 text blocks"
    assert deploy_footer(status, env="test") in joined


def test_layout_sections_and_separator_structure() -> None:
    """A release deploy: the version headlines as a Section, Release accessory.

    The minimal release status (label, kind, ref, deploy_url) renders
    the version as the headline Section with the Release button as
    accessory — no Services action row when there is no branch, tree
    or image to link.
    """
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
    assert kinds.count(TYPE_SEPARATOR) == 2, "one separator between Services and Infra, one before the footer"
    assert kinds.count(TYPE_SECTION) == 1, "the release headline is a Section"
    buttons = _iter_buttons(top)
    assert "Release" in [b["label"] for b in buttons]
    section = next(c for c in top[0]["components"] if c["type"] == TYPE_SECTION)
    assert section["accessory"]["label"] == "Release"


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
    bot = _Bot(["111", "222"])
    config = AnnounceConfig(locale="en", config_dir=CONFIG_DIR)
    await announce_startup(bot, status, config, logs_service=logs, deploy_env="test")  # type: ignore[arg-type]
    assert set(logs.events) == {"111", "222"}
    for events in logs.events.values():
        assert len(events) == 1
        event = events[0]
        assert event.kind == "start"
        assert event.footer.startswith("kingdoms-deploy env=test image=pr-42-x")
        assert isinstance(event.layout, discord.ui.LayoutView)


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
