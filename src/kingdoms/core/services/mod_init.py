"""Mod scaffold generator (kingdoms#178, kingdoms-services#211).

``kingdoms mod init <name>`` generates the full self-contained mod
directory (declaration, package with lifecycle hooks, locale catalogs,
test, README) plus the kingdoms-repo docs pointer set — everything a
designer or agent session needs so a new mod starts structured, not
improvised (docs/GUIDES/mod-init.md in the kingdoms repo).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kingdoms.core.services.mod_layout import MODULAR_ROOT

SPRINT_DOC_URL = (
    "https://github.com/merlin-pinpin-org/kingdoms/blob/main/docs/GUIDES/mod-init.md"
)

DECLARATION_TEMPLATE = """\
# {name} mod declaration (kingdoms mod init).
# Schema (validated by kingdoms.core.services.mod_registry at startup):
#   id            required, simple slug, must match the package name
#   enabled       bool (default true); disabled mods are never loaded
#   seasonal      bool, MANDATORY; the mod type (seasonal or permanent)
#                 branches the admin surface (lifecycle panel + season
#                 salons vs one guild-level config channel)
#   settings      free-form, mod-specific
#   channels      list of {{key, display_name, description, per_instance}}
#   roles         list of {{key, display_name, color, hoisted}}
#   workflows     list of workflow names implemented by the mod
#   commands      list of command names implemented by the mod
#   dependencies  list of mod ids this mod depends on
#   private       bool (default false); a private mod is provisioned and
#                 visible only to its owners and the bot admins
#   owner_discord_ids  list of Discord user ids (mandatory when private)
# Channels are addressed at runtime as "mod:key" (e.g. "{name}:announce").
id: {name}
enabled: true
seasonal: {seasonal}
{privacy_block}settings: {{}}
channels:
  - key: announce
    display_name: {display_name}
    description: Where the {name} mod posts announcements
roles:
  - key: {name}_member
    display_name: {member_role}
workflows: []
commands: []
dependencies: []
"""

ENTRYPOINT_TEMPLATE = '''\
"""{name} mod — entrypoint.

Lifecycle hooks (all optional, all idempotent — see
kingdoms.core.services.mod_entrypoint): the core calls ``register`` at
bot build time, ``setup_hook`` after a restart to re-register persistent
UI, ``close`` on shutdown. Hooks degrade quietly when their stack
(Mongo, Redis, providers) is not configured.
"""

from __future__ import annotations

from typing import Any

import discord

from kingdoms.discord.ui import BLURPLE, Container, Text, UILayout


def register(bot: Any, config: Any) -> None:
    """Wire the mod onto the bot: commands, views, admin sections."""

    @discord.app_commands.command(
        name="{command}",
        description="{display_name} — generated placeholder, replace me"
    )
    async def _cmd(interaction: discord.Interaction) -> None:
        view = UILayout().add(
            Container(accent=BLURPLE).add(
                Text(f"# {display_name}"),
                Text(
                    "This placeholder view comes from `kingdoms mod init` — "
                    "replace it with the mod's real surface."
                ),
            )
        ).build()
        await interaction.response.send_message(view=view, ephemeral=True)

    bot.tree.add_command(_cmd)


def setup_hook(bot: Any) -> None:
    """Re-register persistent UI after a restart (dynamic items, views)."""


def close(bot: Any) -> None:
    """Stop the mod's background tasks (best-effort)."""
'''

LOCALE_TEMPLATE = """\
{locale}:
  {name}:
    title: "{display_name}"
    hello: "Hello from the {name} mod — scaffolded by `kingdoms mod init`."
"""

TEST_TEMPLATE = '''\
"""{name} mod — scaffold acceptance test (MockDiscord, in-memory stores)."""

from __future__ import annotations


def test_{name}_scaffold_imports() -> None:
    """The generated entrypoint module imports against the core seams."""
    from kingdoms.mods import {name}  # noqa: F401
'''

README_TEMPLATE = """\
# {name} (mod)

Scaffolded by `kingdoms mod init` — see the guide:
<{SPRINT_DOC_URL}>

- Declaration: `mod.yaml` (schema validated at startup)
- Package: `src/{name}/` (lifecycle hooks against the core seams)
- Locales: `locales/en.yaml` + `locales/fr.yaml`
- Tests: `tests/`

Canonical rules and environment documentation live in the `kingdoms`
repo: `docs/MODS/{name}/` (README, RULES, ENVIRONMENT) — never here.
"""


@dataclass(frozen=True, slots=True)
class ModInitResult:
    """What `kingdoms mod init` generated."""

    mod_dir: Path
    declaration: Path
    package: Path
    locales: tuple[Path, ...]
    test: Path
    readme: Path


def _privacy_block(private: bool, owner_ids: tuple[str, ...]) -> str:
    """Render the private/owner declaration lines for the template."""
    if not private:
        return ""
    owners = "".join(f'  - "{oid}"\n' for oid in owner_ids)
    return "private: true\nowner_discord_ids:\n" + owners


def _write(path: Path, content: str) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite existing file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def init_mod(
    repo_root: Path,
    name: str,
    *,
    seasonal: bool,
    display_name: str | None = None,
    member_role: str | None = None,
    private: bool = False,
    owner_ids: tuple[str, ...] = (),
) -> ModInitResult:
    """Generate the self-contained mod directory under modules/mods/.

    Raises FileExistsError as soon as any target file exists (the caller
    decides whether to abort); nothing is silently overwritten.
    """
    if not name.replace("_", "").replace("-", "").isalnum() or ":" in name or not name:
        raise ValueError(f"invalid mod id '{name}': must be a simple slug (no ':')")
    if private and not owner_ids:
        raise ValueError("a private mod needs --owner <discord-id> (at least one owner)")
    for owner in owner_ids:
        if not owner.isdigit():
            raise ValueError(f"invalid owner id '{owner}': expected a Discord user id (digits only)")
    mod_dir = Path(repo_root) / MODULAR_ROOT / name
    if mod_dir.exists():
        raise FileExistsError(f"mod directory already exists: {mod_dir}")
    display = display_name or name.replace("_", " ").replace("-", " ").title()
    role = member_role or f"{display} Member"
    context = {
        "name": name,
        "seasonal": "true" if seasonal else "false",
        "display_name": display,
        "member_role": role,
        "command": name.replace("_", "-")[:32],
        "privacy_block": _privacy_block(private, owner_ids),
        "SPRINT_DOC_URL": SPRINT_DOC_URL,
    }
    result = ModInitResult(
        mod_dir=mod_dir,
        declaration=mod_dir / "mod.yaml",
        package=mod_dir / "src" / name / "__init__.py",
        locales=(
            mod_dir / "locales" / "en.yaml",
            mod_dir / "locales" / "fr.yaml",
        ),
        test=mod_dir / "tests" / f"test_{name}.py",
        readme=mod_dir / "README.md",
    )
    _write(result.declaration, DECLARATION_TEMPLATE.format(**context))
    _write(result.package, ENTRYPOINT_TEMPLATE.format(**context))
    for locale_path, locale in zip(result.locales, ("en", "fr"), strict=True):
        _write(locale_path, LOCALE_TEMPLATE.format(locale=locale, **context))
    _write(result.test, TEST_TEMPLATE.format(**context))
    _write(result.readme, README_TEMPLATE.format(**context))
    return result
