"""Unit tests for the startup announcement (kingdoms-services#52).

Covers the four contracts of the announcement: sent with the correct
identity when the channel is configured, skipped silently when absent,
no crash on an unreachable channel, and the machine-readable footer
format asserted exactly (the kingdoms-infra battery parses it).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kingdoms.core.services.mod_registry import ModRegistry
from kingdoms.core.services.status import StatusService
from kingdoms.discord.announce import (
    AnnounceConfig,
    announce_startup,
    build_announcement_embed,
    deploy_footer,
)
from tests.mocks.discord_mock import MockClient, MockTextChannel

CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"


def _status_service(**kwargs: str) -> StatusService:
    from kingdoms.core.services.status import BotAdmins

    return StatusService(registry=ModRegistry({}), bot_admins=BotAdmins(), **kwargs)


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


def test_embed_is_localized_and_carries_footer() -> None:
    status = _status_service(deploy_label="v0.1.0", deploy_kind="release", deploy_ref="v0.1.0")
    config = AnnounceConfig(channel_id="123", locale="en", config_dir=CONFIG_DIR)
    embed = build_announcement_embed(status, config, env="prod")
    assert embed.title == "Kingdoms — Deployment"
    assert "v0.1.0" in (embed.description or "")
    assert "**Environment**: `prod`" in (embed.description or "")
    assert embed.footer is not None
    assert embed.footer.text == deploy_footer(status, env="prod")


def test_embed_falls_back_to_english_for_unknown_locale() -> None:
    status = _status_service(deploy_label="sha-abc1234")
    config = AnnounceConfig(channel_id="123", locale="xx", config_dir=CONFIG_DIR)
    embed = build_announcement_embed(status, config, env="test")
    assert embed.title == "Kingdoms — Deployment"


def test_embed_french_catalog() -> None:
    status = _status_service(deploy_label="v0.1.0")
    config = AnnounceConfig(channel_id="123", locale="fr", config_dir=CONFIG_DIR)
    embed = build_announcement_embed(status, config, env="test")
    assert embed.title == "Kingdoms — Déploiement"
    assert "**Environnement**: `test`" in (embed.description or "")


@pytest.mark.asyncio
async def test_announce_sent_when_channel_configured() -> None:
    status = _status_service(deploy_label="pr-42-x")
    channel = MockTextChannel(id=42, name="deploys")
    client = MockClient(channels=[channel])
    config = AnnounceConfig(channel_id="42", locale="en", config_dir=CONFIG_DIR)
    await announce_startup(client, status, config)  # type: ignore[arg-type]
    assert len(channel.messages) == 1
    message = channel.messages[0]
    assert message.embeds and message.embeds[0].footer is not None
    assert message.embeds[0].footer.text.startswith("kingdoms-deploy env= image=pr-42-x")


@pytest.mark.asyncio
async def test_no_announce_when_channel_absent() -> None:
    status = _status_service()
    channel = MockTextChannel(id=42)
    client = MockClient(channels=[channel])
    config = AnnounceConfig(channel_id="", config_dir=CONFIG_DIR)
    await announce_startup(client, status, config)  # type: ignore[arg-type]
    assert channel.messages == []


@pytest.mark.asyncio
async def test_no_crash_when_fetch_fails() -> None:
    class FailingClient(MockClient):
        async def fetch_channel(self, channel_id: int) -> object:
            raise RuntimeError("network down")

        def get_channel(self, channel_id: int) -> object:
            return None

    status = _status_service()
    client = FailingClient()
    config = AnnounceConfig(channel_id="42", config_dir=CONFIG_DIR)
    await announce_startup(client, status, config)  # type: ignore[arg-type]


def test_announce_config_enabled_requires_digits() -> None:
    assert AnnounceConfig(channel_id="123").enabled is True
    assert AnnounceConfig(channel_id="not-a-number").enabled is False
    assert AnnounceConfig(channel_id="").enabled is False
