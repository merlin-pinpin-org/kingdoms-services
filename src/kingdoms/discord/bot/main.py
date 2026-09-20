"""Bot entry point: loads config, wires core services, starts the Discord client.

The full bot startup is implemented in kingdoms-services#12. The `--preflight`
mode exercises the real startup path (environment, MongoDB, Redis, locale
catalogs) without connecting to the Discord gateway, which requires a valid
token.
"""

from __future__ import annotations

import os
import sys

from kingdoms import __version__


def preflight() -> int:
    """Validate the startup path: environment, dependencies and configs."""
    import pymongo
    import redis
    import yaml

    for var in ("MONGO_URI", "REDIS_URI", "DISCORD_TOKEN"):
        if not os.environ.get(var):
            print(f"PREFLIGHT FAIL: {var} is not set")
            return 1

    try:
        client = pymongo.MongoClient(os.environ["MONGO_URI"], serverSelectionTimeoutMS=5000)
        ping = client.admin.command("ping")
        if ping.get("ok") != 1:
            print(f"PREFLIGHT FAIL: MongoDB ping failed: {ping}")
            return 1
    except Exception as exc:
        print(f"PREFLIGHT FAIL: MongoDB unreachable: {exc}")
        return 1
    print("MongoDB ping OK")

    try:
        r = redis.Redis.from_url(os.environ["REDIS_URI"], socket_connect_timeout=5)
        if r.ping() is not True:
            print("PREFLIGHT FAIL: Redis ping failed")
            return 1
    except Exception as exc:
        print(f"PREFLIGHT FAIL: Redis unreachable: {exc}")
        return 1
    print("Redis ping OK")

    for locale in ("en", "fr"):
        path = f"config/locales/{locale}.yaml"
        try:
            with open(path) as fh:
                catalog = yaml.safe_load(fh)
        except OSError as exc:
            print(f"PREFLIGHT FAIL: cannot read {path}: {exc}")
            return 1
        if not isinstance(catalog, dict) or locale not in catalog:
            print(f"PREFLIGHT FAIL: invalid locale catalog {path}")
            return 1
    print("Locale catalogs OK (en, fr)")

    print(f"PREFLIGHT PASS (kingdoms {__version__})")
    return 0


def main() -> None:
    """Run the Kingdoms Discord bot."""
    raise NotImplementedError("Implemented in kingdoms-services#12")


if __name__ == "__main__":
    if "--preflight" in sys.argv[1:]:
        sys.exit(preflight())
    main()
