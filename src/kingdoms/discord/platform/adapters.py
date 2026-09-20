"""Adapters: convert discord.py objects to core models and back.

Implemented in kingdoms-services#11 (adapters from kingdoms-services#8).
"""

from __future__ import annotations

from kingdoms.core.interfaces.platform import IChannel, IMessage, IUser


def to_core_user(discord_user: object) -> IUser:
    """Convert a discord.py User/Member to the core IUser model."""
    raise NotImplementedError("Implemented in kingdoms-services#11")


def to_core_channel(discord_channel: object) -> IChannel:
    """Convert a discord.py TextChannel to the core IChannel model."""
    raise NotImplementedError("Implemented in kingdoms-services#11")


def to_core_message(discord_message: object) -> IMessage:
    """Convert a discord.py Message to the core IMessage model."""
    raise NotImplementedError("Implemented in kingdoms-services#11")
