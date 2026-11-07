"""Discord rate-limit observability — the buckets, logged.

Discord answers every request with rate-limit headers (X-Ratelimit-Limit,
-Remaining, -Reset-After, -Bucket — see
https://docs.discord.com/developers/topics/rate-limits). discord.py
consumes them internally (its Ratelimit buckets) but logs almost nothing:
the 2026-11-06 channel-creation throttle (~7 h) surfaced as a bare
"see the bot logs".

This module subclasses discord.py's HTTPClient and logs each route's
bucket state after every request:

- DEBUG: the full bucket line (limit, remaining, reset-after, bucket)
- INFO: when a bucket is about to exhaust (remaining <= 1)
- WARNING: when discord.py raises RateLimited (the retry-after with the
  bucket)

The install is DEFENSIVE: it patches the class reference discord.Client
uses, and a discord.py upgrade that changes the internals degrades to
the stock client with a warning — never a crash.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("kingdoms.discord.ratelimit")


def _margin() -> int:
    """Return the admission margin (config/services.yaml: discord.rate_limit_margin).

    A request goes through while remaining > margin; at the margin the
    NEXT request blocks until the bucket resets — the margin absorbs the
    races between concurrent requests (no lock), and leaves headroom for
    the never-starve calls (commands sync, presence).
    """
    try:
        from pathlib import Path

        import yaml

        path = Path(__file__).resolve().parents[3] / "config" / "services.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return int((data.get("discord") or {}).get("rate_limit_margin", 5))
    except Exception:
        return 5


def install() -> None:  # noqa: C901
    """Patch the bot's HTTPClient with the rate-limit-logging subclass.

    Call ONCE, before the discord.Client is instantiated (the bot's
    factory does it at build time).
    """
    import discord
    import discord.client
    import discord.http

    base = discord.http.HTTPClient
    base_ratelimit = discord.http.Ratelimit

    class AdmissionRatelimit(base_ratelimit):  # type: ignore[misc,valid-type]
        """The bucket WITHOUT self-censoring.

        discord.py's acquire() raises RateLimited pre-emptively when the
        bucket's expiry (a PREVIOUS 429's memory) exceeds
        max_ratelimit_timeout — the request never reaches Discord and
        the verdict replays a stale answer (the owner caught it: the
        countdown decremented without any real call).

        Here: the margin is the ONLY gate. remaining > margin → the
        request goes out and DISCORD answers (a real 429 with a live
        Retry-After, or success). remaining <= margin → block until the
        reset. Never a pre-emptive raise on a healthy budget.
        """

        async def acquire(self) -> None:
            import asyncio

            self._last_request = self._loop.time()
            if self.is_expired():
                self.reset()

            margin = _margin()
            # The margin only applies where it IS a margin: on a small
            # bucket (limit <= margin) it would block every call — the
            # exhaustion there rides discord.py's own remaining <= 0 gate.
            if self.limit > margin and self.remaining <= margin and self.expires is not None:
                # at the margin: block until the window resets
                wait = max(0.0, self.expires - self._loop.time())
                logger.warning(
                    "RATE LIMIT ADMISSION: bucket at the margin "
                    "(remaining=%d <= %d) — blocking %.1fs",
                    self.remaining,
                    margin,
                    wait,
                )
                await asyncio.sleep(wait)
                self.reset()
            # else: pass through — Discord decides, for real

    class RateLimitLoggingHTTPClient(base):  # type: ignore[misc,valid-type]
        """The stock client, plus the bucket logging."""

        async def request(self, route: Any, *args: Any, **kwargs: Any) -> Any:
            # The ADMISSION lives in the bucket (AdmissionRatelimit.acquire):
            # remaining > margin → the request goes out, DISCORD answers for
            # real; a RateLimited reaching here is a LIVE 429 (the stale
            # memory can no longer raise pre-emptively).
            try:
                data = await super().request(route, *args, **kwargs)
            except discord.errors.RateLimited as exc:
                bucket = self._bucket_of(route)
                logger.warning(
                    "RATE LIMIT 429: Discord answered 429 on %s %s — "
                    "retry_after=%.0fs %s",
                    route.method,
                    route.url,
                    exc.retry_after,
                    self._bucket_line(bucket),
                )
                raise
            bucket = self._bucket_of(route)
            line = self._bucket_line(bucket)
            remaining = getattr(bucket, "remaining", None)
            if remaining is not None and remaining <= 1:
                logger.info("RATE LIMIT TIGHT: %s %s %s", route.method, route.url, line)
            else:
                logger.debug("RATE LIMIT: %s %s %s", route.method, route.url, line)
            return data

        def _bucket_of(self, route: Any) -> Any:
            """Resolve the route's ratelimit bucket (defensive)."""
            try:
                key = route.key
                major = getattr(route, "major_parameters", "")
                bucket_hash = self._bucket_hashes.get(key)
                bucket_key = (
                    f"{bucket_hash}:{major}" if bucket_hash else f"{key}:{major}"
                )
                return self._buckets.get(bucket_key)
            except Exception:
                return None

        @staticmethod
        def _bucket_line(bucket: Any) -> str:
            """Render the bucket's state (the headers' values)."""
            if bucket is None:
                return "(no bucket yet)"
            limit = getattr(bucket, "limit", "?")
            remaining = getattr(bucket, "remaining", "?")
            reset_after = getattr(bucket, "reset_after", None)
            reset = f"reset_after={reset_after:.1f}s" if reset_after else "reset_after=?"
            return f"bucket limit={limit} remaining={remaining} {reset}"

    # the client's get_ratelimit builds OUR admission buckets
    def _get_ratelimit(self: Any, key: str) -> Any:
        try:
            return self._buckets[key]
        except KeyError:
            self._buckets[key] = AdmissionRatelimit(self.max_ratelimit_timeout)
            return self._buckets[key]

    RateLimitLoggingHTTPClient.get_ratelimit = _get_ratelimit

    try:
        discord.client.HTTPClient = RateLimitLoggingHTTPClient  # type: ignore[attr-defined]
        logger.info("rate-limit observability installed (admission: margin=%d)", _margin())
    except Exception:
        logger.warning("rate-limit observability could not install — the stock client serves", exc_info=True)
