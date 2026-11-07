"""AoE2 binding of the generic game content source seam.

Everything AoE2-specific about content extraction lives here or in
``faction_content`` — the seam itself (``kingdoms.core.games.game_content``)
is game-agnostic and will serve the next game's provider unchanged.
This module resolves AoE2's concrete sources:

- local: the vendored aoe2techtree dataset extractor (default);
- rpc: the ext-aoe2techtree process (``EXT_AOE2TECHTREE_URI``).
"""

from __future__ import annotations

import os
from typing import Any

from kingdoms.core.games.game_content import GameContentSource
from kingdoms.core.games.game_content import LocalizedContent as _LocalizedContent


class ProviderUnavailableError(RuntimeError):
    """A provider the game needs is not configured or unreachable.

    A FUNCTIONAL error: the UI shows it as-is to the admin (the
    provider is down, the sync cannot run) — never a crash.
    """


__all__ = ["LocalizedContent", "ProviderUnavailableError", "resolve_aoe2_content_source"]

LocalizedContent = _LocalizedContent


def resolve_aoe2_content_source(
    provider: Any = None,
    env: dict[str, str] | None = None,
) -> GameContentSource:
    """Resolve AoE2's content source: the aoe2techtree provider process.

    ``EXT_AOE2TECHTREE_URI`` set → the ext-aoe2techtree gRPC process;
    unset → a FUNCTIONAL error (owner direction 2026-11-05: no vendored
    data, the civs come from the provider — a missing provider is an
    operational gap to report, never a silent local fallback).
    """
    from kingdoms.core.games.game_content import resolve_content_source

    lookup = env if env is not None else dict(os.environ)
    uri = lookup.get("EXT_AOE2TECHTREE_URI", "")
    if not uri:
        raise ProviderUnavailableError(
            "the aoe2techtree provider is not configured "
            "(EXT_AOE2TECHTREE_URI) — the civs sync cannot run"
        )
    del provider  # the vendored dataset is gone; the provider serves
    return resolve_content_source(None, uri)
