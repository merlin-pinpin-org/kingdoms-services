"""Smoke test: run inside the bot container against the real MongoDB and Redis.

Verifies that the stack wiring is correct: dependencies reachable, the
kingdoms package importable, and configuration loadable. Exits non-zero on
the first failure.
"""

from __future__ import annotations

import os
import sys

import pymongo
import redis


def check_mongo(uri: str) -> None:
    client = pymongo.MongoClient(uri, serverSelectionTimeoutMS=5000)
    ping = client.admin.command("ping")
    assert ping["ok"] == 1, f"MongoDB ping failed: {ping}"
    print(f"MongoDB ping OK: {uri}")


def check_redis(uri: str) -> None:
    r = redis.Redis.from_url(uri, socket_connect_timeout=5)
    assert r.ping() is True, "Redis ping failed"
    print(f"Redis ping OK: {uri}")


def check_imports() -> None:
    import kingdoms
    import kingdoms.core.enums.channel_category
    import kingdoms.discord.bot.main

    print(f"kingdoms package imports OK: {kingdoms.__version__}")


def check_config() -> None:
    import yaml

    with open("config/locales/en.yaml") as fh:
        en = yaml.safe_load(fh)
    assert "register" in en["en"], "en.yaml missing register keys"
    with open("config/locales/fr.yaml") as fh:
        fr = yaml.safe_load(fh)
    assert "register" in fr["fr"], "fr.yaml missing register keys"
    print("YAML configs load OK (en, fr)")


def main() -> int:
    mongo_uri = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
    redis_uri = os.environ.get("REDIS_URI", "redis://localhost:6379")

    check_mongo(mongo_uri)
    check_redis(redis_uri)
    check_imports()
    check_config()

    print("SMOKE TEST PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
