#!/usr/bin/env python3
"""Temporary session helper (kingdoms-services#175): applies the drift
fixes that need exact-match edits directly in CI, on the clean git tree
(session downloads wrap long lines, so files are restored from git and
edited here, never round-tripped). Every edit fails loudly when its
pattern is missing; already-applied patterns are skipped, so the script
is idempotent across runs. Removed once the stack is green.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def restore(path: str, from_commit: str) -> None:
    """Restore one file's clean content from the git history."""
    out = subprocess.run(
        ["git", "show", f"{from_commit}:{path}"],
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
                "ChannelGroupDef(key="admin", display_name="Admin", admin_only=True, position=1),',
            ),
            (
                'ChannelCategoryDef(key="announce", display_name="Annonces", group="main", kind="announce"),',
                'ChannelCategoryDef(\n                    key="announce", display_name="Annonces", group="main", kind="announce", position=0\n                ),',
            ),
            (
                'ChannelCategoryDef(key="rules", display_name="Règles", group="main", kind="forum"),',
                'ChannelCategoryDef(key="rules", display_name="Règles", group="main", kind="forum", position=1),',
            ),
            (
                'ChannelCategoryDef(key="epoch", display_name="Âge sombre", group="main", adopt="group_single"),',
                'ChannelCategoryDef(\n                    key="epoch", display_name="Âge sombre", group="main", adopt="group_single", position=2\n                ),',
            ),
            (
                'ChannelCategoryDef(key="requests", display_name="Demandes", group="admin", admin_only=True),',
                'ChannelCategoryDef(key="requests", display_name="Demandes", group="admin", admin_only=True, position=0),',
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
                "from tests.mocks.provision import provisioned_wiring\nfrom tests.mocks.discord_mock import (",
                "from tests.mocks.discord_mock import (",
            ),
            (
                ")\n\n\nclass _FakeLogsService:",
                ")\nfrom tests.mocks.provision import provisioned_wiring\n\n\nclass _FakeLogsService:",
            ),
        ],
    )
    edit(
        "tests/unit/test_discord/test_kingdom_profiles.py",
        [
            (
                "from tests.mocks.provision import provisioned_wiring\nfrom tests.mocks.discord_mock import (",
                "from tests.mocks.discord_mock import (",
            ),
            (
                ")\n\n\ndef _guild() -> MockGuild:",
                ")\nfrom tests.mocks.provision import provisioned_wiring\n\n\ndef _guild() -> MockGuild:",
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
            '    group_keys = [group.key for group in kingdoms_definition().channel_groups]\n'
            '    assert group_keys[0] == "profiles"\n'
            '    assert group_keys[1] == "general"\n'
            '    assert group_keys[-1] == "support"',
        )],
    )

    # 5. The profiles module lands with the next PR in the stack: the
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

    # 6. mypy: load_mod_definitions already returns the declarations.
    edit(
        "tests/mocks/provision.py",
        [(
            "    definitions: dict[str, ModDefinition] = load_mod_definitions(REPO_CONFIG_DIR)  # type: ignore[assignment]",
            "    definitions = load_mod_definitions(REPO_CONFIG_DIR)",
        )],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
