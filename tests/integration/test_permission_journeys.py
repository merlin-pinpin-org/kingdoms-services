"""Permission journeys on SimCord — kingdoms-services#55.

These drive the real bot (``create_bot``) through the real dispatch:
a member without the required mod role gets an ephemeral denial and
no side effects; the same member after ``assign_mod_role`` — which
goes through the real discord.py HTTP machinery against SimCord —
executes the action; a bot admin bypasses the mod-level check.
"""

from __future__ import annotations

from typing import Any

import discord
import pytest

from kingdoms.core.services.mod_definition import ModDefinition, RoleDef
from kingdoms.core.services.mod_registry import ModRegistry
from kingdoms.core.services.permissions import PermissionService
from kingdoms.core.services.roles import ModRolesService

JOIN_ID = "clans:join:confirm"


class MappingStore:
    """In-memory RolesDatabase: mappings keyed by guild:mod:role_key."""

    def __init__(self) -> None:
        self.mappings: dict[str, Any] = {}

    async def find_role_mapping(self, guild_id: str, mod: str, role_key: str) -> Any:
        return self.mappings.get(f"{guild_id}:{mod}:{role_key}")

    async def upsert_role_mapping(self, mapping: Any) -> None:
        self.mappings[mapping.id] = mapping

    async def delete_role_mapping(self, guild_id: str, mod: str, role_key: str) -> bool:
        return self.mappings.pop(f"{guild_id}:{mod}:{role_key}", None) is not None


def wire_services(bot: Any, bot_admins: tuple[str, ...] = ()) -> MappingStore:
    """Wire a full-stack PermissionService + ModRolesService on the real bot.

    The seams are the production ones (``DiscordRolesPlatform`` against
    the live guild cache); only the mapping store is in-memory (there
    is no MongoDB on the SimCord journeys).
    """
    from kingdoms.discord.roles_platform import DiscordRolesPlatform

    store = MappingStore()
    registry = ModRegistry(
        {
            "clans": ModDefinition(
                name="clans",
                roles=(RoleDef(key="clan_member", display_name="Clan Member"),),
            )
        }
    )
    platform = DiscordRolesPlatform(bot)
    mod_roles = ModRolesService(database=store, platform=platform, registry=registry, members=platform)
    bot.mod_roles_service = mod_roles
    bot.permission_service = PermissionService(members=platform, roles=mod_roles, bot_admins=bot_admins)
    return store


class TestPermissionJourneys:
    """Click-time authorization through the real dispatch machinery."""

    @pytest.fixture
    def simcord_bot(self, kingdoms_bot):  # type: ignore[no-untyped-def]
        bot = kingdoms_bot
        wire_services(bot)

        @bot.tree.command(name="clanspanel")
        async def clanspanel(interaction: discord.Interaction) -> None:
            view = discord.ui.View()
            button = discord.ui.Button(label="Join the clan", custom_id=JOIN_ID)

            async def join(inter: discord.Interaction) -> None:
                from kingdoms.discord.permissions import require_permission

                service = bot.permission_service
                if service is None:
                    raise RuntimeError("permission service not wired")
                allowed = await require_permission(
                    inter,
                    service,
                    mod="clans",
                    required_roles=("clan_member",),
                    catalog=bot.messages,
                )
                if not allowed:
                    return
                await inter.response.send_message("joined the clan", ephemeral=True)

            button.callback = join
            view.add_item(button)
            await interaction.response.send_message("Clan recruitment", view=view)

        return bot

    @pytest.mark.simcord(strict_sync=False)
    async def test_role_required_journey(self, simcord_env, simcord_bot) -> None:  # type: ignore[no-untyped-def]
        guild = simcord_env.create_guild()
        channel = guild.create_text_channel("general")
        guild.create_role("Clan Member")
        alice = guild.add_member(simcord_env.create_user("alice"))

        await alice.slash(channel, "clanspanel")
        message = channel.last_message
        assert message is not None

        result = await alice.click(message, custom_id=JOIN_ID)
        assert result.ephemeral
        assert "not allowed" in result.response.content.lower()
        assert channel.last_message.id == message.id, "a denial must not post to the channel"

        mod_roles = simcord_bot.mod_roles_service
        if mod_roles is None:
            raise RuntimeError("mod roles service not wired")
        await mod_roles.assign_mod_role(str(guild.id), str(alice.id), "clans", "clan_member")

        result = await alice.click(message, custom_id=JOIN_ID)
        assert result.ephemeral
        assert result.response.content == "joined the clan"

    @pytest.mark.simcord(strict_sync=False)
    async def test_bot_admin_bypasses_mod_roles(self, simcord_env, simcord_bot) -> None:  # type: ignore[no-untyped-def]
        from kingdoms.discord.roles_platform import DiscordRolesPlatform

        guild = simcord_env.create_guild()
        channel = guild.create_text_channel("general")
        guild.create_role("Clan Member")
        admin = guild.add_member(simcord_env.create_user("root"))
        platform = DiscordRolesPlatform(simcord_bot)
        simcord_bot.permission_service = PermissionService(
            members=platform,
            roles=simcord_bot.mod_roles_service,
            bot_admins=(str(admin.id),),
        )

        await admin.slash(channel, "clanspanel")
        message = channel.last_message
        assert message is not None
        result = await admin.click(message, custom_id=JOIN_ID)
        assert result.response.content == "joined the clan"

    @pytest.mark.simcord(strict_sync=False)
    async def test_open_action_executes_for_everyone(self, simcord_env, simcord_bot) -> None:  # type: ignore[no-untyped-def]
        guild = simcord_env.create_guild()
        channel = guild.create_text_channel("general")
        alice = guild.add_member(simcord_env.create_user("alice"))

        await alice.slash(channel, "clanspanel")
        message = channel.last_message
        assert message is not None

        from kingdoms.core.services.permissions import ActionContext

        service = simcord_bot.permission_service
        if service is None:
            raise RuntimeError("permission service not wired")
        ctx = ActionContext(
            user_id=str(alice.id),
            mod="clans",
            custom_id=JOIN_ID,
            guild_id=str(guild.id),
        )
        result = await service.is_authorized(ctx)
        assert result.allowed
        assert result.reason == "open"
