#!/usr/bin/env python3
"""Temporary session helper (kingdoms-services#175): applies the drift
fixes that need exact-match edits directly in CI, on the clean git tree
(session downloads wrap long lines, so files are restored from git and
edited here, never round-tripped). Every edit fails loudly when its
pattern is missing; already-applied patterns are skipped, so the script
is idempotent across runs. Removed once the stack is green.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GIT = shutil.which("git") or "/usr/bin/git"


def restore(path: str, from_commit: str) -> None:
    """Restore one file's clean content from the git history."""
    out = subprocess.run(
        [GIT, "show", f"{from_commit}:{path}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    (ROOT / path).write_text(out.stdout, encoding="utf-8")
    print(f"restored {path} from {from_commit}")


def edit(path: str, replacements: list[tuple[str, str]]) -> None:
    """Apply exact-match replacements; skip already-applied ones."""
    p = ROOT / path
    text = p.read_text(encoding="utf-8")
    for old, new in replacements:
        if old in text:
            text = text.replace(old, new)
            print(f"applied edit in {path}: {old[:60]!r}")
        elif new in text:
            print(f"already applied in {path}: {new[:60]!r}")
        else:
            print(f"FATAL: pattern not found in {path}: {old[:80]!r}")
            sys.exit(1)
    p.write_text(text, encoding="utf-8")


def main() -> int:
    # 1. The channel test file was corrupted by a wrapped download in a
    #    previous session push: restore its clean content from git, then
    #    declare explicit positions in the structured declaration fixture.
    restore("tests/unit/test_core/test_channel_service.py", "e1ed9bc^")
    edit(
        "tests/unit/test_core/test_channel_service.py",
        [
            (
                'ChannelGroupDef(key="admin", display_name="Admin", admin_only=True),',
                'ChannelGroupDef('
                'key="admin", display_name="Admin", admin_only=True, position=1'
                '),',
            ),
            (
                'ChannelCategoryDef(key="announce", display_name="Annonces", '
                'group="main", kind="announce"),',
                'ChannelCategoryDef(\n'
                '                    key="announce", display_name="Annonces", '
                'group="main", kind="announce", position=0\n'
                '                ),',
            ),
            (
                'ChannelCategoryDef(key="rules", display_name="Règles", '
                'group="main", kind="forum"),',
                'ChannelCategoryDef(key="rules", display_name="Règles", '
                'group="main", kind="forum", position=1),',
            ),
            (
                'ChannelCategoryDef(key="epoch", display_name="Âge sombre", '
                'group="main", adopt="group_single"),',
                'ChannelCategoryDef(\n'
                '                    key="epoch", display_name="Âge sombre", '
                'group="main", adopt="group_single", position=2\n'
                '                ),',
            ),
            (
                'ChannelCategoryDef(key="requests", display_name="Demandes", '
                'group="admin", admin_only=True),',
                'ChannelCategoryDef(key="requests", display_name="Demandes", '
                'group="admin", admin_only=True, position=0),',
            ),
            (
                'assert "Annonces" in report.created',
                'assert "Salons/Annonces" in report.created',
            ),
        ],
    )

    # 2. Quoted type annotation (ruff UP037).
    edit(
        "tests/unit/test_core/test_mod_registry.py",
        [(
            'def _write_mod(tmp_path: "Path", body: str) -> None:',
            "def _write_mod(tmp_path: Path, body: str) -> None:",
        )],
    )

    # 3. Import order (ruff I001): discord_mock before provision.
    edit(
        "tests/unit/test_discord/test_kingdom_panels.py",
        [
            (
                "from tests.mocks.provision import provisioned_wiring\n"
                "from tests.mocks.discord_mock import (",
                "from tests.mocks.discord_mock import (",
            ),
            (
                ")\n\n\nclass _FakeLogsService:",
                ")\nfrom tests.mocks.provision import provisioned_wiring\n"
                "\n\nclass _FakeLogsService:",
            ),
        ],
    )
    edit(
        "tests/unit/test_discord/test_kingdom_profiles.py",
        [
            (
                "from tests.mocks.provision import provisioned_wiring\n"
                "from tests.mocks.discord_mock import (",
                "from tests.mocks.discord_mock import (",
            ),
            (
                ")\n\n\ndef _guild() -> MockGuild:",
                ")\nfrom tests.mocks.provision import provisioned_wiring\n"
                "\n\ndef _guild() -> MockGuild:",
            ),
        ],
    )

    # 4. RUF015: index a named list instead of slicing a comprehension.
    edit(
        "tests/unit/test_discord/test_kingdom_setup.py",
        [(
            '    groups = list(kingdoms_definition().channel_groups)\n'
            '    assert [group.key for group in groups][0] == "profiles"\n'
            '    assert [group.key for group in groups][1] == "general"\n'
            '    assert [group.key for group in groups][-1] == "support"',
            '    group_keys = [group.key for group in '
            'kingdoms_definition().channel_groups]\n'
            '    assert group_keys[0] == "profiles"\n'
            '    assert group_keys[1] == "general"\n'
            '    assert group_keys[-1] == "support"',
        )],
    )

    # 5. The groups variable went away with the RUF015 edit: resolve the
    #    admin group lookup straight from the declaration (ruff F821).
    edit(
        "tests/unit/test_discord/test_kingdom_setup.py",
        [(
            '    admin = next(group for group in groups if group.key == "admin")',
            '    admin = next(\n'
            '        group\n'
            '        for group in kingdoms_definition().channel_groups\n'
            '        if group.key == "admin"\n'
            '    )',
        )],
    )

    # 6. The profiles module lands with the next PR in the stack: the
    #    panels test that touches it skips until then.
    edit(
        "tests/unit/test_discord/test_kingdom_panels.py",
        [(
            '    """A submitted application provisions the private profile channel."""\n'
            "    from kingdoms.discord.kingdom_profiles import PROFILES_CATEGORY",
            '    """A submitted application provisions the private profile channel."""\n'
            '    pytest.importorskip("kingdoms.discord.kingdom_profiles")\n'
            "    from kingdoms.discord.kingdom_profiles import PROFILES_CATEGORY",
        )],
    )

    # 7. mypy: load_mod_definitions already returns the declarations.
    edit(
        "tests/mocks/provision.py",
        [(
            "    definitions: dict[str, ModDefinition] = "
            "load_mod_definitions(REPO_CONFIG_DIR)  # type: ignore[assignment]",
            "    definitions = load_mod_definitions(REPO_CONFIG_DIR)",
        )],
    )

    # 8. D401: imperative docstring (ruff).
    edit(
        "src/kingdoms/core/services/channel.py",
        [(
            '        """The platform\'s structured seam, when it implements it."""',
            '        """Return the platform\'s structured seam, when it implements it."""',
        )],
    )

    # 9. C901: split _resolve_channel_id into focused helpers.
    edit(
        "src/kingdoms/core/services/channel.py",
        [
            (
                "        name, kind, group_key, admin_only, position, adopt = "
                "self._spec_for_category(\n"
                "            category, as_group=as_group\n"
                "        )",
                "        name, _kind, group_key, _admin_only, _position, _adopt = "
                "self._spec_for_category(\n"
                "            category, as_group=as_group\n"
                "        )",
            ),
            (
                '        if as_group:\n'
                '            if structured is None:\n'
                '                # Legacy platform without the structured seam: the group\n'
                '                # degrades to a flat channel under its display name.\n'
                '                return await self._resolve_flat(guild_id, category, name, sink)\n'
                '            found = await structured.find_group_by_name(guild_id, name)\n'
                '            if found is not None:\n'
                '                if sink is not None:\n'
                '                    sink[category] = "adopted"\n'
                '                return found\n'
                '            created_group = await structured.create_group(guild_id, name, position, admin_only)\n'
                '            if sink is not None:\n'
                '                sink[category] = "created"\n'
                '            return created_group\n',
                '        if as_group:\n'
                '            return await self._resolve_group_category(\n'
                '                guild_id, category, structured, sink\n'
                '            )\n',
            ),
            (
                '        group_id: str | None = None\n'
                '        group_admin_only = False\n'
                '        if structured is not None and group_key:\n'
                '            mod_name = category.partition(":")[0]\n'
                '            group = self._registry.require(mod_name).channel_group(group_key)\n'
                '            group_id = await self._resolve_group_id(guild_id, group)\n'
                '            group_admin_only = group.admin_only\n'
                '\n'
                '        if structured is not None and group_id is not None:\n'
                '            found = await structured.find_channel_of_kind(guild_id, name, kind, group_id)\n'
                '            if found is None and adopt == "group_single":\n'
                '                found = await structured.single_channel_in_group(guild_id, group_id, kind)\n'
                '            if found is not None:\n'
                '                await self._persist(guild_id, category, found, name)\n'
                '                if sink is not None:\n'
                '                    sink[category] = "adopted"\n'
                '                return found\n'
                '            created = await structured.create_channel_of_kind(\n'
                '                guild_id, name, kind, group_id, admin_only or group_admin_only, position\n'
                '            )\n'
                '            await self._persist(guild_id, category, created, name)\n'
                '            if sink is not None:\n'
                '                sink[category] = "created"\n'
                '            return created\n'
                '\n'
                '        return await self._resolve_flat(guild_id, category, name, sink)\n',
                '        if structured is not None and group_key:\n'
                '            resolved = await self._resolve_in_group(\n'
                '                guild_id, category, group_key, structured, sink\n'
                '            )\n'
                '            if resolved is not None:\n'
                '                return resolved\n'
                '\n'
                '        return await self._resolve_flat(guild_id, category, name, sink)\n',
            ),
            (
                '    async def _resolve_flat(\n'
                '        self, guild_id: str, category: str, name: str, sink: dict[str, str] | None\n'
                '    ) -> str:',
                '    async def _resolve_group_category(\n'
                '        self,\n'
                '        guild_id: str,\n'
                '        category: str,\n'
                '        structured: StructuredChannelsPlatform | None,\n'
                '        sink: dict[str, str] | None,\n'
                '    ) -> str:\n'
                '        """Resolve a declared group (a container, never persisted) to its id."""\n'
                '        name, _kind, _group, admin_only, position, _adopt = self._spec_for_category(\n'
                '            category, as_group=True\n'
                '        )\n'
                '        if structured is None:\n'
                '            # Legacy platform without the structured seam: the group\n'
                '            # degrades to a flat channel under its display name.\n'
                '            return await self._resolve_flat(guild_id, category, name, sink)\n'
                '        found = await structured.find_group_by_name(guild_id, name)\n'
                '        if found is not None:\n'
                '            if sink is not None:\n'
                '                sink[category] = "adopted"\n'
                '            return found\n'
                '        created_group = await structured.create_group(guild_id, name, position, admin_only)\n'
                '        if sink is not None:\n'
                '            sink[category] = "created"\n'
                '        return created_group\n'
                '\n'
                '    async def _resolve_in_group(\n'
                '        self,\n'
                '        guild_id: str,\n'
                '        category: str,\n'
                '        group_key: str,\n'
                '        structured: StructuredChannelsPlatform,\n'
                '        sink: dict[str, str] | None,\n'
                '    ) -> str | None:\n'
                '        """Resolve a channel inside its declared group; None to fall back flat."""\n'
                '        name, kind, _group_key, admin_only, position, adopt = self._spec_for_category(\n'
                '            category, as_group=False\n'
                '        )\n'
                '        mod_name = category.partition(":")[0]\n'
                '        group = self._registry.require(mod_name).channel_group(group_key)\n'
                '        group_id = await self._resolve_group_id(guild_id, group)\n'
                '        if group_id is None:\n'
                '            return None\n'
                '        found = await structured.find_channel_of_kind(guild_id, name, kind, group_id)\n'
                '        if found is None and adopt == "group_single":\n'
                '            found = await structured.single_channel_in_group(guild_id, group_id, kind)\n'
                '        if found is not None:\n'
                '            await self._persist(guild_id, category, found, name)\n'
                '            if sink is not None:\n'
                '                sink[category] = "adopted"\n'
                '            return found\n'
                '        created = await structured.create_channel_of_kind(\n'
                '            guild_id, name, kind, group_id, admin_only or group.admin_only, position\n'
                '        )\n'
                '        await self._persist(guild_id, category, created, name)\n'
                '        if sink is not None:\n'
                '            sink[category] = "created"\n'
                '        return created\n'
                '\n'
                '    async def _resolve_flat(\n'
                '        self, guild_id: str, category: str, name: str, sink: dict[str, str] | None\n'
                '    ) -> str:',
            ),
        ],
    )

    # 10. channels_platform.py: D401 docstrings and mypy guards.
    edit(
        "src/kingdoms/discord/channels_platform.py",
        [
            (
                '        """The guild\'s live channels of a declared kind (forum vs text)."""',
                '        """Return the guild\'s live channels of a declared kind (forum vs text)."""',
            ),
            (
                '        """The group\'s single channel of a kind, whatever its name; '
                'None otherwise."""',
                '        """Return the group\'s single channel of a kind, whatever its '
                'name; None otherwise."""',
            ),
            (
                '        category = guild.get_channel(int(group_id)) if group_id and '
                'group_id.isdigit() else None',
                '        fetched = guild.get_channel(int(group_id)) if group_id and '
                'group_id.isdigit() else None\n'
                '        category = fetched if isinstance(fetched, '
                'discord.CategoryChannel) else None',
            ),
            (
                '        return str(found.id) if found is not None else None',
                '        return str(getattr(found, "id")) if found is not None else None',
            ),
        ],
    )

    # 11. kingdom_persistent.py: C901 — split _run_reset into helpers.
    edit(
        "src/kingdoms/discord/kingdom_persistent.py",
        [(
            'async def _run_reset(interaction: discord.Interaction, strings: dict[str, str]) -> None:\n'
            '    """Delete every kingdoms channel/category, then report."""\n'
            '    from kingdoms.discord.kingdom_setup import MOD_NAME, _slug\n'
            '\n'
            '    guild = interaction.guild\n'
            '    if guild is None:\n'
            '        await interaction.response.send_message(strings["no_channel"], ephemeral=True)\n'
            '        return\n'
            '    await interaction.response.defer(ephemeral=True)\n'
            '    deleted = 0\n'
            '    try:\n'
            '        wiring = _wiring()\n'
            '        structure_names: set[str] = set()\n'
            '        registry = getattr(wiring, "registry", None)\n'
            '        mod = None\n'
            '        if registry is not None:\n'
            '            try:\n'
            '                mod = registry.require(MOD_NAME)\n'
            '            except Exception:\n'
            '                logger.warning("KINGDOMS ADMIN: mod registry lookup failed", exc_info=True)\n'
            '                mod = None\n'
            '        if mod is not None:\n'
            '            for group in mod.channel_groups:\n'
            '                structure_names.add(_slug(group.display_name))\n'
            '            for category in mod.channel_categories:\n'
            '                structure_names.add(_slug(category.display_name))\n'
            '        channels = [*list(guild.text_channels), *list(getattr(guild, "forums", []))]\n'
            '        categories = list(getattr(guild, "categories", []))\n'
            '        for channel in channels:\n'
            '            if _slug(channel.name) in structure_names or _slug(channel.name).startswith("profil-"):\n'
            '                try:\n'
            '                    await channel.delete()\n'
            '                    deleted += 1\n'
            '                except Exception:\n'
            '                    logger.warning("KINGDOMS ADMIN: channel delete failed", exc_info=True)\n'
            '        for category in categories:\n'
            '            if _slug(category.name) in structure_names:\n'
            '                try:\n'
            '                    await category.delete()\n'
            '                    deleted += 1\n'
            '                except Exception:\n'
            '                    logger.warning("KINGDOMS ADMIN: category delete failed", exc_info=True)\n'
            '    except Exception:\n'
            '        logger.exception("KINGDOMS ADMIN: salons reset failed for guild %s", guild.id)\n'
            '        await interaction.followup.send(strings["reset_failed"], ephemeral=True)\n'
            '        return\n'
            '    await interaction.followup.send(strings["reset_done"].format(deleted), ephemeral=True)\n',
            'async def _run_reset(interaction: discord.Interaction, strings: dict[str, str]) -> None:\n'
            '    """Delete every kingdoms channel/category, then report."""\n'
            '    guild = interaction.guild\n'
            '    if guild is None:\n'
            '        await interaction.response.send_message(strings["no_channel"], ephemeral=True)\n'
            '        return\n'
            '    await interaction.response.defer(ephemeral=True)\n'
            '    try:\n'
            '        wiring = _wiring()\n'
            '        structure_names = _declared_structure_slugs(getattr(wiring, "registry", None))\n'
            '        deleted = await _delete_matching_channels(guild, structure_names)\n'
            '        deleted += await _delete_matching_categories(guild, structure_names)\n'
            '    except Exception:\n'
            '        logger.exception("KINGDOMS ADMIN: salons reset failed for guild %s", guild.id)\n'
            '        await interaction.followup.send(strings["reset_failed"], ephemeral=True)\n'
            '        return\n'
            '    await interaction.followup.send(strings["reset_done"].format(deleted), ephemeral=True)\n'
            '\n'
            '\n'
            'def _declared_structure_slugs(registry: Any) -> set[str]:\n'
            '    """Slugs of every declared group and channel name; empty set when unknown."""\n'
            '    from kingdoms.discord.kingdom_setup import MOD_NAME, _slug\n'
            '\n'
            '    names: set[str] = set()\n'
            '    if registry is None:\n'
            '        return names\n'
            '    try:\n'
            '        mod = registry.require(MOD_NAME)\n'
            '    except Exception:\n'
            '        logger.warning("KINGDOMS ADMIN: mod registry lookup failed", exc_info=True)\n'
            '        return names\n'
            '    for group in mod.channel_groups:\n'
            '        names.add(_slug(group.display_name))\n'
            '    for category in mod.channel_categories:\n'
            '        names.add(_slug(category.display_name))\n'
            '    return names\n'
            '\n'
            '\n'
            'async def _delete_matching_channels(guild: discord.Guild, structure_names: set[str]) -> int:\n'
            '    """Delete the declared channels (and profile channels); return the count."""\n'
            '    from kingdoms.discord.kingdom_setup import _slug\n'
            '\n'
            '    deleted = 0\n'
            '    channels = [*list(guild.text_channels), *list(getattr(guild, "forums", []))]\n'
            '    for channel in channels:\n'
            '        slug = _slug(channel.name)\n'
            '        if slug in structure_names or slug.startswith("profil-"):\n'
            '            try:\n'
            '                await channel.delete()\n'
            '                deleted += 1\n'
            '            except Exception:\n'
            '                logger.warning("KINGDOMS ADMIN: channel delete failed", exc_info=True)\n'
            '    return deleted\n'
            '\n'
            '\n'
            'async def _delete_matching_categories(guild: discord.Guild, structure_names: set[str]) -> int:\n'
            '    """Delete the declared categories; return the count."""\n'
            '    from kingdoms.discord.kingdom_setup import _slug\n'
            '\n'
            '    deleted = 0\n'
            '    for category in getattr(guild, "categories", []):\n'
            '        if _slug(category.name) in structure_names:\n'
            '            try:\n'
            '                await category.delete()\n'
            '                deleted += 1\n'
            '            except Exception:\n'
            '                logger.warning("KINGDOMS ADMIN: category delete failed", exc_info=True)\n'
            '    return deleted\n',
        )],
    )

    # 12. The mod declaration now carries the salons-first channel
    #     categories: the validated-set test expects them all.
    edit(
        "tests/unit/test_kingdoms_mod.py",
        [(
            '        assert {c.key for c in kingdoms.channel_categories} == {\n'
            '            "attack",\n'
            '            "attack_delays",\n'
            '            "cadastre",\n'
            '            "diplomacy",\n'
            '            "geopolitics",\n'
            '        }',
            '        assert {c.key for c in kingdoms.channel_categories} == {\n'
            '            "announce",\n'
            '            "applications",\n'
            '            "apply",\n'
            '            "attack",\n'
            '            "attack_delays",\n'
            '            "bug",\n'
            '            "cadastre",\n'
            '            "diplomacy",\n'
            '            "epoch",\n'
            '            "exploration",\n'
            '            "geopolitics",\n'
            '            "lords",\n'
            '            "patrol",\n'
            '            "presentation",\n'
            '            "question",\n'
            '            "requests",\n'
            '            "rules",\n'
            '            "season",\n'
            '            "settings",\n'
            '            "suggestions",\n'
            '            "talks",\n'
            '            "tavern",\n'
            '            "territory",\n'
            '            "update",\n'
            '        }',
        )],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
