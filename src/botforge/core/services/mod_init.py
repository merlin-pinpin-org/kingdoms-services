"""Mod scaffold generator (botforge-services#178, botforge-services#211).

``botforge mod init <name>`` generates the full self-contained mod
directory (§7 target layout — conf, grpc, cogs, tests, docs pointer)
plus the botforge repo docs pointer set — everything a designer or agent
session needs so a new mod starts structured, not improvised
(docs/GUIDES/mod-init.md in the botforge repo).

The three §7 concerns are separated from day one:

- ``conf/`` — the declaration (mod.yaml) and the locale catalogs
- ``grpc/<name>/`` — the mod's service package: its contract
  (contract.py) and its implementation (service.py); no Discord import
- ``cogs/<name>/`` — the Discord cog: commands and views, calling the
  service through the contract (``mod_service``); never the data
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from botforge.core.services.mod_layout import MODULAR_ROOT

SPRINT_DOC_URL = (
    "https://github.com/jj-guild/botforge/blob/main/docs/GUIDES/mod-init.md"
)

DECLARATION_TEMPLATE = """\
# {name} mod declaration (botforge mod init).
# Schema (validated by botforge.core.services.mod_registry at startup):
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
commands:
  - {command}
dependencies: []
"""

CONTRACT_TEMPLATE = '''\
"""The {name} mod's service contract (§7).

The ONLY surface the cog may call: the service implements it, the cog
consumes it. Everything else in the service package is private — the
cog never touches the mod's collections or internals directly.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class {class_name}Service(Protocol):
    """What the {name} mod's service exposes to its cog."""

    async def status(self, guild_id: str) -> dict[str, Any]:
        """Return the mod's status for the guild (the cog renders it)."""
        ...
'''

SERVICE_TEMPLATE = '''\
"""The {name} mod's service (§7 grpc): its data, its collections.

Implements the contract (contract.py). Never imports Discord — the cog
is the mod's only Discord layer.
"""

from __future__ import annotations

from typing import Any

from .contract import {class_name}Service as {class_name}Contract


class {class_name}ServiceImpl({class_name}Contract):
    """The {name} mod's service implementation (in-process today)."""

    async def status(self, guild_id: str) -> dict[str, Any]:
        """Return the mod's status for the guild (the cog renders it)."""
        return {{"mod": "{name}", "guild": guild_id, "state": "scaffold"}}
'''

SERVICE_INIT_TEMPLATE = '''\
"""The {name} mod's gRPC service package (§7 grpc).

Its data, its collections, its contract. The cog calls this package
through the contract — never the other way around.
"""

from __future__ import annotations

from .contract import {class_name}Service
from .service import {class_name}ServiceImpl

__all__ = ["{class_name}Service", "{class_name}ServiceImpl"]
'''

COG_TEMPLATE = '''\
"""The {name} mod's Discord cog (§7): commands and views.

The cog is the mod's ONLY Discord layer. It calls the mod's service
through the contract (``mod_service``) and builds its UI through the
botforge UI SDK — never the service's internals.
"""

from __future__ import annotations

import discord

from botforge.core.services.mod_entrypoint import mod_service
from botforge.discord.ui import BLURPLE, Container, Text, UILayout


async def status_view(guild_id: str) -> discord.ui.LayoutView:
    """Render the service's status through the UI SDK."""
    service = mod_service("{name}")
    status = await service.{class_name}ServiceImpl().status(guild_id)
    return (
        UILayout()
        .add(
            Container(accent=BLURPLE).add(
                Text("# {display_name}"),
                Text(f"state: {{status.get('state', 'unknown')}}"),
            )
        )
        .build()
    )
'''

COG_INIT_TEMPLATE = '''\
"""The {name} mod's cog package (§7 cogs) — the mod's Discord layer.

Lifecycle hooks (all optional, all idempotent — see
botforge.core.services.mod_entrypoint): the core calls ``register`` at
bot build time, ``setup_hook`` after a restart to re-register persistent
UI, ``close`` on shutdown. Hooks degrade quietly when their stack
(Mongo, Redis, providers) is not configured.
"""

from __future__ import annotations

from typing import Any

import discord

from .cog import status_view


def register(bot: Any, config: Any) -> None:
    """Wire the cog onto the bot: commands, views, admin sections."""

    @discord.app_commands.command(
        name="{command}",
        description="{display_name} — generated placeholder, replace me",
    )
    async def _cmd(interaction: discord.Interaction) -> None:
        view = await status_view(str(interaction.guild_id or ""))
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
    hello: "Hello from the {name} mod — scaffolded by `botforge mod init`."
"""

TEST_TEMPLATE = '''\
"""{name} mod — scaffold acceptance test (MockDiscord, in-memory stores)."""

from __future__ import annotations


def test_{name}_cog_package_loads() -> None:
    """The generated cog package imports against the core seams."""
    from botforge.mods.{name} import cogs  # noqa: F401


def test_{name}_service_implements_the_contract() -> None:
    """The service package exposes the contract's implementation."""
    from botforge.mods.{name}.grpc import {class_name}ServiceImpl
    from botforge.mods.{name}.grpc.contract import {class_name}Service

    impl = {class_name}ServiceImpl()
    assert isinstance(impl, {class_name}Service)
'''


README_TEMPLATE = """\
# {name} (mod)

Scaffolded by `botforge mod init` — see the guide:
<{SPRINT_DOC_URL}>

- Declaration: `conf/mod.yaml` (schema validated at startup)
- Service: `grpc/{name}/` (its data, its collections, its contract)
- Cog: `cogs/{name}/` (the Discord layer — calls the service through
  the contract)
- Locales: `conf/locales/en.yaml` + `conf/locales/fr.yaml`
- Tests: `tests/`

Canonical rules and environment documentation live in the `botforge`
repo: `docs/MODS/{name}/` (README, RULES, ENVIRONMENT) — never here.
"""


@dataclass(frozen=True, slots=True)
class ModInitResult:
    """What `botforge mod init` generated."""

    mod_dir: Path
    declaration: Path
    package: Path
    service: Path
    contract: Path
    cogs: Path
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
    """Generate the self-contained §7 mod directory under modules/mods/.

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
        "class_name": "".join(part.capitalize() for part in name.replace("-", "_").split("_")),
        "seasonal": "true" if seasonal else "false",
        "display_name": display,
        "member_role": role,
        "command": name.replace("_", "-")[:32],
        "privacy_block": _privacy_block(private, owner_ids),
        "SPRINT_DOC_URL": SPRINT_DOC_URL,
    }
    result = ModInitResult(
        mod_dir=mod_dir,
        declaration=mod_dir / "conf" / "mod.yaml",
        package=mod_dir / "cogs" / name / "__init__.py",
        service=mod_dir / "grpc" / name / "service.py",
        contract=mod_dir / "grpc" / name / "contract.py",
        cogs=mod_dir / "cogs" / name / "cog.py",
        locales=(
            mod_dir / "conf" / "locales" / "en.yaml",
            mod_dir / "conf" / "locales" / "fr.yaml",
        ),
        test=mod_dir / "tests" / f"test_{name}.py",
        readme=mod_dir / "README.md",
    )
    _write(result.declaration, DECLARATION_TEMPLATE.format(**context))
    _write(result.contract, CONTRACT_TEMPLATE.format(**context))
    _write(result.service, SERVICE_TEMPLATE.format(**context))
    _write(result.service.parent / "__init__.py", SERVICE_INIT_TEMPLATE.format(**context))
    _write(result.cogs, COG_TEMPLATE.format(**context))
    _write(result.package, COG_INIT_TEMPLATE.format(**context))
    for locale_path, locale in zip(result.locales, ("en", "fr"), strict=True):
        _write(locale_path, LOCALE_TEMPLATE.format(locale=locale, **context))
    _write(result.test, TEST_TEMPLATE.format(**context))
    _write(result.readme, README_TEMPLATE.format(**context))
    return result
