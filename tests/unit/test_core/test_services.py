"""Unit tests for the core services skeletons."""

from __future__ import annotations

import pytest

from kingdoms.core.services.channel import ChannelService
from kingdoms.core.services.state import StateService
from kingdoms.core.services.workflow import WorkflowEngine


async def test_channel_service_raises_until_implemented() -> None:
    service = ChannelService(platform=None)  # type: ignore[arg-type]
    with pytest.raises(NotImplementedError):
        await service.get_channel_for_category("guild-1", "ladder:ladder_rankings")


async def test_workflow_engine_raises_until_implemented() -> None:
    engine = WorkflowEngine()
    with pytest.raises(NotImplementedError):
        await engine.start_workflow(None, {})  # type: ignore[arg-type]


async def test_state_service_raises_until_implemented() -> None:
    service = StateService()
    with pytest.raises(NotImplementedError):
        await service.get("wf-1")
    with pytest.raises(NotImplementedError):
        await service.set("wf-1", {"step": "ask_name"})
