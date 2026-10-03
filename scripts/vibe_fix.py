"""Vibe session fix loop (kingdoms-services#175, PR #171).

Pass 3: refactors _run_reset into helpers (C901) while keeping the
branch semantics, and aligns the epoch rename tests with the mock's
Discord-faithful slugified channel names.
"""
from __future__ import annotations

import pathlib

PERSISTENT = "src/kingdoms/discord/kingdom_persistent.py"
CONTENT_TESTS = "tests/unit/test_discord/test_kingdom_content.py"

OLD_RESET = '''async def _run_reset(interaction: discord.Interaction, strings: dict[str, str]) -> None:
    """Delete every kingdoms channel/category, then report."""
    from kingdoms.discord.kingdom_setup import MOD_NAME, _slug

    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(strings["no_channel"], ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    deleted = 0
    try:
        wiring = _wiring()
        structure_names: set[str] = set()
        registry = getattr(wiring, "registry", None)
        mod = None
        if registry is not None:
            try:
                mod = registry.require(MOD_NAME)
            except Exception:
                logger.warning("KINGDOMS ADMIN: mod registry lookup failed", exc_info=True)
                mod = None
        if mod is not None:
            for group in mod.channel_groups:
                structure_names.add(_slug(group.display_name))
            for category in mod.channel_categories:
                structure_names.add(_slug(category.display_name))
        channels = [*list(guild.text_channels), *list(getattr(guild, "forums", []))]
        categories = list(getattr(guild, "categories", []))
        for channel in channels:
            category = getattr(channel, "category", None)
            in_epoch = category is not None and _slug(getattr(category, "name", "")) == _slug("Époque")
            if in_epoch or _slug(channel.name) in structure_names or _slug(channel.name).startswith("profil-"):
                try:
                    await channel.delete()
                    deleted += 1
                except Exception:
                    logger.warning("KINGDOMS ADMIN: channel delete failed", exc_info=True)
        for category in categories:
            if _slug(category.name) in structure_names:
                try:
                    await category.delete()
                    deleted += 1
                except Exception:
                    logger.warning("KINGDOMS ADMIN: category delete failed", exc_info=True)
    except Exception:
        logger.exception("KINGDOMS ADMIN: salons reset failed for guild %s", guild.id)
        await interaction.followup.send(strings["reset_failed"], ephemeral=True)
        return
    await interaction.followup.send(strings["reset_done"].format(deleted), ephemeral=True)'''

NEW_RESET = '''async def _run_reset(interaction: discord.Interaction, strings: dict[str, str]) -> None:
    """Delete every kingdoms channel/category, then report."""
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(strings["no_channel"], ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        wiring = _wiring()
        structure_names = _declared_structure_slugs(getattr(wiring, "registry", None))
        deleted = await _delete_matching_channels(guild, structure_names)
        deleted += await _delete_matching_categories(guild, structure_names)
    except Exception:
        logger.exception("KINGDOMS ADMIN: salons reset failed for guild %s", guild.id)
        await interaction.followup.send(strings["reset_failed"], ephemeral=True)
        return
    await interaction.followup.send(strings["reset_done"].format(deleted), ephemeral=True)


def _declared_structure_slugs(registry: Any) -> set[str]:
    """Slugs of every declared group and category name; empty set when unknown."""
    from kingdoms.discord.kingdom_setup import MOD_NAME, _slug

    names: set[str] = set()
    if registry is None:
        return names
    try:
        mod = registry.require(MOD_NAME)
    except Exception:
        logger.warning("KINGDOMS ADMIN: mod registry lookup failed", exc_info=True)
        return names
    for group in mod.channel_groups:
        names.add(_slug(group.display_name))
    for category in mod.channel_categories:
        names.add(_slug(category.display_name))
    return names


async def _delete_matching_channels(guild: discord.Guild, structure_names: set[str]) -> int:
    """Delete the structure channels, the profile channels and the epoch members."""
    from kingdoms.discord.kingdom_setup import _slug

    deleted = 0
    channels = [*list(guild.text_channels), *list(getattr(guild, "forums", []))]
    for channel in channels:
        category = getattr(channel, "category", None)
        in_epoch = (
            category is not None and _slug(getattr(category, "name", "")) == _slug("Époque")
        )
        name = _slug(channel.name)
        if in_epoch or name in structure_names or name.startswith("profil-"):
            try:
                await channel.delete()
                deleted += 1
            except Exception:
                logger.warning("KINGDOMS ADMIN: channel delete failed", exc_info=True)
    return deleted


async def _delete_matching_categories(guild: discord.Guild, structure_names: set[str]) -> int:
    """Delete the declared structure categories."""
    from kingdoms.discord.kingdom_setup import _slug

    deleted = 0
    for category in list(getattr(guild, "categories", [])):
        if _slug(category.name) in structure_names:
            try:
                await category.delete()
                deleted += 1
            except Exception:
                logger.warning("KINGDOMS ADMIN: category delete failed", exc_info=True)
    return deleted'''

OLD_NAME1 = '''    assert epoch.name == "Âge féodal"'''
NEW_NAME1 = '''    assert epoch.name == "age-feodal"'''

OLD_NAME2 = '''    assert any(c.name == "Âge féodal" for c in guild.text_channels)'''
NEW_NAME2 = '''    assert any(c.name == "age-feodal" for c in guild.text_channels)'''

OLD_NAME3 = '''    assert "Âge féodal" not in remaining'''
NEW_NAME3 = '''    assert "age-feodal" not in remaining'''


def edit(path: str, old: str, new: str, label: str) -> None:
    p = pathlib.Path(path)
    text = p.read_text()
    if new in text:
        print("vibe_fix: " + label + ": already applied")
        return
    if old not in text:
        print("vibe_fix: FATAL " + label + ": pattern not found")
        return
    p.write_text(text.replace(old, new, 1))
    print("vibe_fix: " + label + ": applied")


def main() -> None:
    p = pathlib.Path(PERSISTENT)
    if "_declared_structure_slugs" in p.read_text():
        print("vibe_fix: reset refactor: already applied")
    else:
        edit(PERSISTENT, OLD_RESET, NEW_RESET, "reset refactor")
    edit(CONTENT_TESTS, OLD_NAME1, NEW_NAME1, "epoch rename slug 1")
    edit(CONTENT_TESTS, OLD_NAME2, NEW_NAME2, "epoch rename slug 2")
    edit(CONTENT_TESTS, OLD_NAME3, NEW_NAME3, "epoch rename slug 3")


if __name__ == "__main__":
    main()
