"""Token bucket rate limiting with Redis or a bounded-lifetime local fallback."""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass

from scout_api.core.config import Settings, get_settings
from scout_api.modules.crawler.core.redis_client import (
    RedisGateway,
    build_redis_gateway,
)

logger = logging.getLogger(__name__)

# Redis TIME avoids clock skew between workers; all decisions are atomic.
_TOKEN_BUCKET_LUA = """
local capacity = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local clock = redis.call('TIME')
local now = tonumber(clock[1]) + tonumber(clock[2]) / 1000000
local state = redis.call('HMGET', KEYS[1], 'tokens', 'updated_at')
local tokens = tonumber(state[1]) or capacity
local updated = tonumber(state[2]) or now
now = math.max(now, updated)
local rate = capacity / window
tokens = math.min(capacity, tokens + (now - updated) * rate)
local allowed = 0
if tokens >= 1 - 0.000000001 then
  tokens = math.max(0, tokens - 1)
  allowed = 1
end
local retry = 0
if allowed == 0 then
  retry = math.max(1, math.ceil((1 - tokens) / rate))
end
redis.call('HSET', KEYS[1], 'tokens', tokens, 'updated_at', now)
redis.call('EXPIRE', KEYS[1], math.ceil(window))
return {allowed, math.floor(tokens), retry}
"""


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    retry_after: int
    scope: str


class _MemoryBucket:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[str, tuple[float, float, float]] = {}
        self._next_cleanup = 0.0

    def hit(self, key: str, limit: int, window_seconds: int) -> RateLimitResult:
        with self._lock:
            now = time.monotonic()
            if now >= self._next_cleanup:
                self._buckets = {k: v for k, v in self._buckets.items() if v[2] > now}
                self._next_cleanup = now + 60
            tokens, updated_at, _ = self._buckets.get(key, (float(limit), now, now))
            rate = limit / window_seconds
            tokens = min(float(limit), tokens + max(0, now - updated_at) * rate)
            allowed = tokens >= 1 - 1e-9
            if allowed:
                tokens = max(0, tokens - 1)
            self._buckets[key] = (tokens, now, now + window_seconds)
            return RateLimitResult(
                allowed=allowed,
                limit=limit,
                remaining=max(0, math.floor(tokens)),
                retry_after=0 if allowed else max(1, math.ceil((1 - tokens) / rate)),
                scope=key.split(":", 1)[0],
            )


class RateLimiter:
    """API client rate limiter (not marketplace crawl throttling)."""

    def __init__(
        self,
        settings: Settings | None = None,
        redis_gateway: RedisGateway | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._redis = redis_gateway
        if self._redis is None and self._settings.redis_url:
            self._redis = build_redis_gateway(self._settings)
        self._memory = _MemoryBucket()
        self._next_fallback_warning = 0.0

    def check(
        self,
        *,
        scope: str,
        identity: str,
        limit: int,
        window_seconds: int = 60,
    ) -> RateLimitResult:
        if not self._settings.rate_limit_enabled:
            return RateLimitResult(
                allowed=True,
                limit=limit,
                remaining=limit,
                retry_after=0,
                scope=scope,
            )
        if limit <= 0 or window_seconds <= 0:
            raise ValueError("Rate limit capacity and window must be positive")
        # Separate namespace: old fixed-window keys contain strings, not hashes.
        key = f"rl:tb:{scope}:{identity}"
        if self._redis is not None:
            client = self._redis.get_client()
            if client is not None:
                try:
                    raw = client.eval(_TOKEN_BUCKET_LUA, 1, key, limit, window_seconds)
                    return RateLimitResult(
                        allowed=bool(int(raw[0])),
                        limit=limit,
                        remaining=max(0, int(raw[1])),
                        retry_after=max(0, int(raw[2])),
                        scope=scope,
                    )
                except Exception:
                    pass
            now = time.monotonic()
            if now >= self._next_fallback_warning:
                logger.warning("rate_limit_local_fallback", extra={"scope": scope})
                self._next_fallback_warning = now + 60
        result = self._memory.hit(key, limit, window_seconds)
        return RateLimitResult(
            allowed=result.allowed,
            limit=result.limit,
            remaining=result.remaining,
            retry_after=result.retry_after,
            scope=scope,
        )


_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter()
    return _limiter


def reset_rate_limiter() -> None:
    global _limiter
    _limiter = None
