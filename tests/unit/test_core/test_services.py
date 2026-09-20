"""Unit tests for the core services skeletons."""

from __future__ import annotations

import pytest

from kingdoms.core.services.channel import ChannelService
from kingdoms.core.services.workflow import WorkflowEngine


async def test_channel_service_setup_mod_channels_raises_until_implemented() -> None:
    service = ChannelService(platform=None)  # type: ignore[arg-type]
    with pytest.raises(NotImplementedError):
        await service.setup_mod_channels("guild-1", "example")


async def test_channel_service_raises_until_implemented() -> None:
    service = ChannelService(platform=None)  # type: ignore[arg-type]
    with pytest.raises(NotImplementedError):
        await service.get_channel_for_category("guild-1", "admin")


async def test_workflow_engine_raises_until_implemented() -> None:
    engine = WorkflowEngine()
    with pytest.raises(NotImplementedError):
        await engine.start_workflow(None, {})  # type: ignore[arg-type]
