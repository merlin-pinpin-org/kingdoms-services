"""Unit tests for the MongoDB models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from kingdoms.core.models.channel import ChannelModel
from kingdoms.core.models.guild import GuildModel
from kingdoms.core.models.user import GameProfile, UserModel
from kingdoms.core.models.workflow import WorkflowState


def test_user_model_defaults() -> None:
    user = UserModel(
        _id="user-1",
        platform="discord",
        platform_user_id="123456789",
        display_name="PlayerOne",
    )
    assert user.id == "user-1"
    assert user.locale == "en"
    assert user.game_profiles == {}


def test_user_model_accepts_game_profiles() -> None:
    user = UserModel(
        _id="user-1",
        platform="discord",
        platform_user_id="123456789",
        display_name="PlayerOne",
        game_profiles={"aoe2": GameProfile(game_id="aoe2", data={"rating": 1200})},
    )
    assert user.game_profiles["aoe2"].data["rating"] == 1200


def test_user_model_requires_fields() -> None:
    with pytest.raises(ValidationError):
        UserModel(_id="user-1")  # type: ignore[call-arg]


def test_guild_model_defaults() -> None:
    guild = GuildModel(_id="guild-1", platform="discord")
    assert guild.locale == "en"
    assert guild.channel_categories == {}
    assert guild.role_keys == {}


def test_channel_model_fields() -> None:
    channel = ChannelModel(
        _id="channel-1",
        guild_id="guild-1",
        platform="discord",
        category="example:announce",
        channel_id="987654321",
        name="Annonces",
    )
    assert channel.category == "example:announce"


def test_workflow_state_defaults() -> None:
    state = WorkflowState(
        _id="wf-1",
        workflow_name="Annonces",
        guild_id="guild-1",
        user_id="user-1",
        current_step="ask_name",
        status="IN_PROGRESS",
    )
    assert state.payload == {}
