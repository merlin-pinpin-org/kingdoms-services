"""svc-core gRPC server (ADR-0020 bootstrap seam).

Serves the kingdoms.v1 contracts from ``contracts/``. The first service
is Status (cross-process wiring assert); ladder/game services land with
their issues.
"""
from __future__ import annotations

import logging
import os

from kingdoms import __version__
from kingdoms.core.rpc.status import CoreStatus, build_status_server

logger = logging.getLogger("kingdoms.core_process")


def main() -> None:
    """Run the svc-core gRPC server."""
    import asyncio

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    port = os.environ.get("CORE_GRPC_PORT", "50051")
    asyncio.run(_serve(port))


async def _serve(port: str) -> None:
    server = build_status_server(
        lambda: CoreStatus(version=__version__, process="svc-core"),
    )
    bind = f"[::]:{port}"
    server.add_insecure_port(bind)
    await server.start()
    logger.info("SVC_CORE_READY port=%s version=%s", port, __version__)
    await server.wait_for_termination()


if __name__ == "__main__":
    main()
