"""The layering guard: the five architecture rules, enforced on the AST.

R1 — the ext providers are isolated (code and run): they may import only
      shared utils (ids, debug, rate-limit, upstream identity, gRPC
      plumbing, codecs, timing, wiki naming) — never game code, never
      discord, never another provider's internals.
R2 — a game service knows its providers by NAME, never by URL: no
      EXT_*_URI environment reads inside a game package.
R3 — core never mentions a provider: no provider name, no EXT_*_URI.
R4 — discord (the bot, the mods) talks to game services and core only:
      no ext import, no provider client, no EXT_*_URI.
R5 — every Mongo collection has ONE owner; a package only touches its
      own collections.

Violations are computed from the import graph and compared against the
ALLOWLIST (the audit's findings, frozen and dated). Every chantier
shrinks the allowlist; a NEW violation fails the test with a diff.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "kingdoms"

# The shared utils the ext providers may import (R1's whitelist).
_SHARED_UTILS = (
    "kingdoms",
    "kingdoms.core.ids",
    "kingdoms.core.debug",
    "kingdoms.core.rate_limit",
    "kingdoms.core.upstream_identity",
    "kingdoms.core.rpc",
    "kingdoms.core.models",
    "kingdoms.aoe2",
    "kingdoms.core.wiki_names",
    "kingdoms.core_process.provider_endpoints",
    "kingdoms.core_process",
)

_PROVIDER_NAMES = ("liquipedia", "aoe2lobby", "librematch", "aoe2techtree")


def _imports_of(path: Path) -> set[str]:
    """Every module imported by one file (top-level, ast-based)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return set()
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def _env_reads(path: Path) -> set[str]:
    """Every EXT_*_URI environment variable name read by one file."""
    return set(re.findall(r"EXT_[A-Z0-9]+_URI", path.read_text(encoding="utf-8")))


def _package(path: Path) -> str:
    return ".".join(path.relative_to(SRC.parent).with_suffix("").parts[:-1])


def _python_files(*subdirs: str) -> list[Path]:
    files: list[Path] = []
    for d in subdirs:
        files.extend(sorted((SRC / d).rglob("*.py")))
    return files


def _assert_allowed(offenders: list[str], allowlist: tuple[str, ...], rule: str) -> None:
    """A violation passes only while its allowlist entry stands.

    A NEW violation fails with its name; an allowlist entry without its
    violation fails too (the allowlist must SHRINK, never rot).
    """
    allowed = sorted(allowlist)
    fresh = sorted(set(offenders) - set(allowed))
    stale = sorted(set(allowlist) - set(offenders))
    assert not fresh, f"{rule} — NEW violations:\n" + "\n".join(fresh)
    assert not stale, f"{rule} — allowlist entries to REMOVE (fixed!):\n" + "\n".join(stale)



ALLOWLIST_R1 = (

)

ALLOWLIST_R3 = (

)

ALLOWLIST_R4 = (

)


def test_r1_ext_imports_only_shared_utils() -> None:
    """R1: no ext package imports game/discord/foreign-provider code."""
    offenders: list[str] = []
    for path in _python_files("ext_aoe2lobby", "ext_librematch", "ext_liquipedia", "ext_aoe2techtree"):
        own = f"kingdoms.{path.parent.name}"
        for module in sorted(_imports_of(path)):
            if not module.startswith("kingdoms"):
                continue
            if module.startswith(own) or module in _SHARED_UTILS or any(
                module.startswith(u + ".") for u in _SHARED_UTILS if u != "kingdoms"
            ):
                continue
            offenders.append(f"{path.relative_to(SRC.parent)} -> {module}")
    _assert_allowed(offenders, ALLOWLIST_R1, "ext providers import beyond the shared utils")


def test_r3_core_mentions_no_provider() -> None:
    """R3: core never mentions a provider by name or URI."""
    offenders: list[str] = []
    for path in _python_files("core"):
        text = path.read_text(encoding="utf-8")
        for name in _PROVIDER_NAMES:
            if re.search(rf"\b{name}\b", text):
                offenders.append(f"{path.relative_to(SRC.parent)} mentions '{name}'")
    _assert_allowed(offenders, ALLOWLIST_R3, "core mentions providers")


def test_r4_discord_never_touches_providers() -> None:
    """R4: the bot/mods talk to game services and core — never an ext."""
    offenders: list[str] = []
    for path in _python_files("discord"):
        for module in sorted(_imports_of(path)):
            if module.startswith("kingdoms.ext_"):
                offenders.append(f"{path.relative_to(SRC.parent)} -> {module}")
        offenders.extend(
            f"{path.relative_to(SRC.parent)} reads {env}"
            for env in sorted(_env_reads(path))
        )
    _assert_allowed(offenders, ALLOWLIST_R4, "discord touches providers directly")


# ---------------------------------------------------------------------------
# R2 — a game service knows its providers by NAME, never by URL: no
# upstream API endpoint hardcoded in a game package (the wiring is the
# process environment). Attribution LINKS (a map's source page) are UI
# data, not endpoints — they ride the provider's data.
# ---------------------------------------------------------------------------
_UPSTREAM_ENDPOINT_RE = re.compile(r"(api[.]php|wss?://)")


def test_r2_game_code_hardcodes_no_upstream_endpoint() -> None:
    """R2: no upstream API endpoint inside the game packages."""
    offenders: list[str] = []
    for path in _python_files("game_aoe2"):
        for match in _UPSTREAM_ENDPOINT_RE.finditer(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.relative_to(SRC.parent)} hardcodes {match.group(0)!r}")
    _assert_allowed(offenders, ALLOWLIST_R2, "game code hardcodes an upstream endpoint")


ALLOWLIST_R2: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# R5 — every Mongo collection has ONE owner: the bot never touches a
# collection raw; it goes through the owning service (in-process for the
# platform's own, gRPC for the game's).
# ---------------------------------------------------------------------------
_RAW_COLLECTION_RE = re.compile(r"(?:database|db)\[")


_STORAGE_ADAPTER_RE = re.compile(r"class Mongo[A-Za-z]*Database")


def test_r5_discord_reads_no_collection_raw() -> None:
    """R5: no raw collection access in discord — the services own them.

    The ONE exemption is by rule, not by allowlist: the storage adapters
    (``class Mongo*Database``) are the collections' owner
    IMPLEMENTATIONS — the ports core's services consume. A consumer
    file touching a collection raw is a violation; an adapter file is
    the storage layer doing its job.
    """
    offenders: list[str] = []
    for path in _python_files("discord"):
        text = path.read_text(encoding="utf-8")
        if not _RAW_COLLECTION_RE.search(text):
            continue
        if _STORAGE_ADAPTER_RE.search(text):
            continue  # the storage adapter: the owner's implementation
        offenders.append(f"{path.relative_to(SRC.parent)} touches a collection raw")
    _assert_allowed(offenders, ALLOWLIST_R5, "discord reads a collection raw")


# The dated, documented gaps (the audit trail — every chantier shrinks it):
# The dated, documented gap (the audit trail — every chantier shrinks it):
ALLOWLIST_R5: tuple[str, ...] = (
    # The purge tool: an operational maintenance script (a destructive
    # purge guarded by a Mongo marker) — not a feature path.
    "kingdoms/discord/catalog_purge.py touches a collection raw",
)
