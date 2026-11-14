"""The kingdom mod's gRPC service package (§7 grpc).

Its data, its collections, its contract. The cog calls this package
through the contract — never the other way around.
"""

from __future__ import annotations

from .contract import KingdomService
from .service import KingdomServiceImpl

__all__ = ["KingdomService", "KingdomServiceImpl"]
