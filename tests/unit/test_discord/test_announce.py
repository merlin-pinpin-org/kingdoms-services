"""Unit tests for the startup announcement (kingdoms-services#52, #109).

The announcement is the "start" lifecycle event, delivered by the core
LogService to each guild's 🤖-bot-logs channel. These tests pin the
frozen footer format, the embed rendering contract (same helpers as
/status — Services and Infra lines) and the wiring contracts: with a
LogService the event flows to every guild; without one (local runs,
unit tests) the announcement degrades to a silent skip.
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
    announcement_fallback_text,
    build_announcement_embed,
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


def test_embed_reuses_the_status_rendering() -> None:
    status = _status_service(
        deploy_label="pr-42-x",
        deploy_kind="pr",
        deploy_ref="42",
        deploy_url="https://github.com/merlin-pinpin-org/kingdoms-services/pull/42#issuecomment-1",
        deploy_image="ghcr.io/merlin-pinpin-org/kingdoms-services:pr-42-x",
    )
    config = AnnounceConfig(locale="en", config_dir=CONFIG_DIR)
    embed = build_announcement_embed(status, config, env="test")
    assert isinstance(embed, discord.Embed)
    assert embed.title == "Kingdoms — Deployment"
    assert embed.description == "`test`"
    fields = {f.name: f.value for f in embed.fields}
    assert "Pull-request [#42](" in fields["Services"]
    assert "Image [pr-42-x](" in fields["Services"]
    assert "Infra" in fields


def test_embed_is_localized() -> None:
    status = _status_service(deploy_label="v0.1.0")
    config = AnnounceConfig(locale="fr", config_dir=CONFIG_DIR)
    embed = build_announcement_embed(status, config, env="prod")
    assert embed.title == "Kingdoms — Déploiement"
    assert embed.fields[0].value == "v0.1.0"


def test_embed_falls_back_to_english_for_unknown_locale() -> None:
    status = _status_service(deploy_label="sha-abc1234")
    config = AnnounceConfig(locale="xx", config_dir=CONFIG_DIR)
    embed = build_announcement_embed(status, config, env="test")
    assert embed.title == "Kingdoms — Deployment"


def test_fallback_text_carries_the_identity() -> None:
    status = _status_service(deploy_label="pr-42-x")
    assert "pr-42-x" in announcement_fallback_text(status, env="test")
    assert _status_service().deploy_label == ""


@pytest.mark.asyncio
async def test_announce_delivers_start_event_to_every_guild() -> None:
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
        assert isinstance(event.embed, discord.Embed)
        assert "pr-42-x" in event.message


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
    assert event.embed is None
    assert LogService is not None
