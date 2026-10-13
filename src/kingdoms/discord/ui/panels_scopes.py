"""The platform's channel-tree scopes — the reference tree, declared.

One declaration per scope folder of docs/architecture/channel-tree.md
(the kingdoms repo is the source of truth): `bot` (private), `games`,
`games/aoe2`, `mods`. Each folder carries its channels with stable
keys, i18n labels and access bands. Every resolution is persisted at
provisioning time (stored channel ids) — the syncs and the pins
resolve by those stored ids, never by the translated name.

Registering at boot is enough: the provisioning applies the tree
rules (canonical order, guild-locale naming, read-only menu channels,
admin visibility), idempotently, in every guild.
"""

from __future__ import annotations

from kingdoms.discord.ui.panels import ChannelDecl, ScopeSpec

BOT_SCOPE = ScopeSpec(
    scope="bot",
    category_key="ui.panels.categories.bot",
    channels=(
        ChannelDecl(
            key="logs",
            label_key="ui.panels.channels.logs",
            access="admin"
        ),
        ChannelDecl(
            key="admin",
            label_key="ui.panels.channels.admin",
            access="admin"
        ),
        ChannelDecl(
            key="decisions",
            label_key="ui.panels.channels.decisions",
            access="admin",
        ),
    ),
)

GAMES_SCOPE = ScopeSpec(
    scope="games",
    category_key="ui.panels.categories.games",
    channels=(
        ChannelDecl(key="general", label_key="ui.panels.channels.general", access="readonly"),
        ChannelDecl(key="discussions", label_key="ui.panels.channels.discussions"),
        ChannelDecl(key="admin", label_key="ui.panels.channels.admin", access="admin"),
        ChannelDecl(key="decisions", label_key="ui.panels.channels.decisions", access="admin"),
    ),
)

AOE2_SCOPE = ScopeSpec(
    scope="games/aoe2",
    category_key="ui.panels.categories.games_aoe2",
    channels=(
        ChannelDecl(key="general", label_key="ui.panels.channels.general", access="readonly"),
        ChannelDecl(key="discussions", label_key="ui.panels.channels.discussions"),
        ChannelDecl(
            key="live",
            label_key="ui.panels.channels.live",
            access="readonly"
        ),
        ChannelDecl(
            key="civs",
            label_key="ui.panels.channels.civs",
            kind="forum",
            access="readonly"
        ),
        ChannelDecl(
            key="maps",
            label_key="ui.panels.channels.maps",
            kind="forum",
            access="readonly"
        ),
        ChannelDecl(
            key="map-pools",
            label_key="ui.panels.channels.map_pools",
            kind="forum",
            access="readonly"
        ),
        ChannelDecl(key="admin", label_key="ui.panels.channels.admin", access="admin"),
        ChannelDecl(key="decisions", label_key="ui.panels.channels.decisions", access="admin"),
    ),
)

MODS_SCOPE = ScopeSpec(
    scope="mods",
    category_key="ui.panels.categories.mods",
    channels=(
        ChannelDecl(key="general", label_key="ui.panels.channels.general", access="readonly"),
        ChannelDecl(key="discussions", label_key="ui.panels.channels.discussions"),
        ChannelDecl(key="admin", label_key="ui.panels.channels.admin", access="admin"),
        ChannelDecl(key="decisions", label_key="ui.panels.channels.decisions", access="admin"),
    ),
)


def register_platform_scopes() -> None:
    """Register the platform's reference scopes (idempotent, boot-time)."""
    from kingdoms.discord.ui.panels import register_scope

    for spec in (BOT_SCOPE, GAMES_SCOPE, AOE2_SCOPE, MODS_SCOPE):
        register_scope(spec)


__all__ = [
    "AOE2_SCOPE",
    "BOT_SCOPE",
    "GAMES_SCOPE",
    "MODS_SCOPE",
    "register_platform_scopes",
]
