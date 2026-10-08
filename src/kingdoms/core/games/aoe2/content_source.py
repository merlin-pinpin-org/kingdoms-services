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

from kingdoms.core.games.aoe2.faction_content import TechtreeContentProvider
from kingdoms.core.games.game_content import GameContentSource, LocalProviderSource
from kingdoms.core.games.game_content import LocalizedContent as _LocalizedContent

__all__ = ["LocalizedContent", "resolve_aoe2_content_source"]

LocalizedContent = _LocalizedContent


def resolve_aoe2_content_source(
    provider: TechtreeContentProvider,
    env: dict[str, str] | None = None,
) -> GameContentSource:
    """Resolve AoE2's content source from the environment.

    ``EXT_AOE2TECHTREE_URI`` set → the ext-aoe2techtree gRPC process;
    unset → the vendored dataset read in-process. The generic seam does
    the picking; only the env name and the provider are AoE2's.
    """
    from kingdoms.core.games.game_content import resolve_content_source

    lookup = env if env is not None else dict(os.environ)
    return resolve_content_source(
        LocalProviderSource(provider),
        lookup.get("EXT_AOE2TECHTREE_URI", ""),
    )
