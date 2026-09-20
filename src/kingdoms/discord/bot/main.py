"""Bot entry point: loads config, wires core services, starts the Discord client.

The ``--preflight`` mode exercises the real startup path (environment,
MongoDB, Redis, locale catalogs) without connecting to the Discord gateway,
which requires a valid token. The default mode connects to the gateway and
logs a distinctive ready line that CI smoke tests assert on
(kingdoms-services#12, kingdoms-services#34).
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from kingdoms import __version__

READY_LOG_LINE = "KINGDOMS_BOT_READY"


def _build_logger() -> logging.Logger:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    return logging.getLogger("kingdoms.bot")


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
        client: pymongo.MongoClient[dict[str, object]] = pymongo.MongoClient(
            os.environ["MONGO_URI"], serverSelectionTimeoutMS=5000
        )
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


async def run_bot() -> None:
    """Connect the bot to the Discord gateway and block until shutdown."""
    import discord

    token = os.environ["DISCORD_TOKEN"]
    intents = discord.Intents.default()
    client = discord.Client(intents=intents)
    logger = _build_logger()

    @client.event
    async def on_ready() -> None:
        logger.info(
            "%s version=%s user=%s guilds=%d",
            READY_LOG_LINE,
            __version__,
            client.user,
            len(client.guilds),
        )

    try:
        await client.login(token)
    except Exception:
        logger.exception("DISCORD LOGIN FAILED (invalid token or network)")
        raise
    await client.connect()


def main() -> None:
    """Run the Kingdoms Discord bot."""
    try:
        asyncio.run(run_bot())
    except (KeyboardInterrupt, SystemExit):
        pass
    except Exception:
        sys.exit(1)


if __name__ == "__main__":
    if "--preflight" in sys.argv[1:]:
        sys.exit(preflight())
    main()
