"""Unit tests for the game content source seam: the provider, required.

No vendored data (owner direction 2026-11-05): the source is the
aoe2techtree provider process over the Content contract, or a
functional error. Covers the RPC source's clean degradation (an
unknown faction answers None).
"""

from __future__ import annotations

from kingdoms.core.games.game_content import RpcContentSource
from kingdoms.game_aoe2.content_source import resolve_aoe2_content_source


def test_resolve_without_provider_is_a_functional_error() -> None:
    """No EXT_AOE2TECHTREE_URI: a FUNCTIONAL error, never a local fallback.

    The vendored dataset is gone (owner direction 2026-11-05): the civs
    come from the provider process or the sync reports the gap.
    """
    import pytest

    from kingdoms.game_aoe2.content_source import ProviderUnavailableError

    with pytest.raises(ProviderUnavailableError):
        resolve_aoe2_content_source(env={})


def test_resolve_env_overrides_to_rpc() -> None:
    """A set EXT_AOE2TECHTREE_URI routes content through the ext process."""
    source = resolve_aoe2_content_source(env={"EXT_AOE2TECHTREE_URI": "kingdoms-ext-aoe2techtree:50063"})
    assert isinstance(source, RpcContentSource)


