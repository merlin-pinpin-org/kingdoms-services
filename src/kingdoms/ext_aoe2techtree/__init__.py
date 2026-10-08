"""ext-aoe2techtree — AoE2 game content provider process.

Serves the kingdoms.v1.Content contract to svc-core: localized faction
and map content extracted from the aoe2techtree dataset (SiegeEngineers,
MIT licence, data extracted from the game under Microsoft's Game Content
Usage Rules). The upstream is an abstraction (``UpstreamContentSource``): the
vendored dataset ships by default, an HTTP API source can replace it via
``AOE2TECHTREE_SOURCE=api`` without touching the contract or the
consumers — switching to another API is an env change, not a code change.
"""

from __future__ import annotations

PROVIDER_ID = "ext-aoe2techtree"
