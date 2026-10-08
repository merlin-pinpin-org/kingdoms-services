"""ext-aoe2techtree process entrypoint (kingdoms.v1.Content provider).

Serves localized AoE2 faction/map content to svc-core over gRPC. The
upstream is resolved through the abstract ``UpstreamContentSource`` seam
(``AOE2TECHTREE_SOURCE``: vendored dataset by default, HTTP API as a
drop-in replacement) — the contract and the core never change when the
upstream does.
"""

from __future__ import annotations

import asyncio
import logging
import os


def main() -> None:
    """Run the ext-aoe2techtree gRPC content provider server."""
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    from kingdoms.core_process.content_server import serve_content_provider
    from kingdoms.ext_aoe2techtree import PROVIDER_ID
    from kingdoms.ext_aoe2techtree.source import (
        DEFAULT_DATASET_DIR,
        SUPPORTED_LOCALES,
        resolve_source,
    )

    source = resolve_source(
        os.environ.get("AOE2TECHTREE_SOURCE", "dataset"),
        dataset_dir=DEFAULT_DATASET_DIR,
        api_url=os.environ.get("AOE2TECHTREE_API_URL", ""),
        api_key=os.environ.get("AOE2TECHTREE_API_KEY", ""),
    )
    asyncio.run(
        serve_content_provider(
            "aoe2techtree",
            "aoe2",
            SUPPORTED_LOCALES,
            PROVIDER_ID,
            source,
        )
    )


if __name__ == "__main__":
    main()
