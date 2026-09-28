"""svc-core process entrypoint (ADR-0020).

The core process owns the domain, MongoDB and Redis, and serves the
gRPC seams consumed by bot-discord and ext-* provider processes.
"""
