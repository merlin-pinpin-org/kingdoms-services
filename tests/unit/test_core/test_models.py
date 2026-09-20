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
        game_profiles={
            "aoe2": GameProfile(game_id="aoe2", in_game_name="PlayerOne"),
            "chess": GameProfile(game_id="chess"),
        },
    )
    assert user.game_profiles["aoe2"].in_game_name == "PlayerOne"
    assert user.game_profiles["chess"].in_game_name is None
    assert user.game_profiles["chess"].joined_at is not None


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


def test_user_model_mongo_round_trip() -> None:
    user = UserModel(
        _id="user-1",
        platform="discord",
        platform_user_id="123456789",
        display_name="PlayerOne",
        game_profiles={"aoe2": GameProfile(game_id="aoe2", in_game_name="PlayerOne")},
    )
    doc = user.to_mongo()
    assert "_id" in doc
    assert "id" not in doc
    assert doc["_id"] == "user-1"
    restored = UserModel.from_mongo(doc)
    assert restored == user


def test_guild_model_mongo_round_trip() -> None:
    guild = GuildModel(
        _id="guild-1",
        platform="discord",
        locale="fr",
        channel_categories={"announce": "987654321"},
        role_keys={"admin": "111111111"},
    )
    doc = guild.to_mongo()
    assert doc["_id"] == "guild-1"
    assert GuildModel.from_mongo(doc) == guild


def test_channel_model_mongo_round_trip() -> None:
    channel = ChannelModel(
        _id="channel-1",
        guild_id="guild-1",
        platform="discord",
        category="example:announce",
        channel_id="987654321",
        name="Annonces",
    )
    doc = channel.to_mongo()
    assert doc["_id"] == "channel-1"
    assert ChannelModel.from_mongo(doc) == channel


def test_workflow_state_mongo_round_trip() -> None:
    state = WorkflowState(
        _id="wf-1",
        workflow_name="registration",
        guild_id="guild-1",
        user_id="user-1",
        current_step="ask_name",
        status="IN_PROGRESS",
        payload={"name": "PlayerOne", "attempts": 2},
    )
    doc = state.to_mongo()
    assert doc["_id"] == "wf-1"
    assert WorkflowState.from_mongo(doc) == state


def test_workflow_state_from_mongo_rejects_strict_type_violations() -> None:
    with pytest.raises(ValidationError):
        WorkflowState.from_mongo(
            {
                "_id": "wf-1",
                "workflow_name": "registration",
                "guild_id": "guild-1",
                "user_id": "user-1",
                "current_step": 42,
                "status": "IN_PROGRESS",
                "payload": {"attempts": 2},
            }
        )
