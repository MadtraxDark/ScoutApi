"""Distributed single-flight via Redis SET NX PX + compare-and-delete ownership."""

from __future__ import annotations

import logging
import random
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from redis.exceptions import RedisError

from .redis_client import RedisGateway
from .redis_keys import flight_lock_key

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Compare-and-delete: only the owner token may release the lock.
_RELEASE_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""


@dataclass(frozen=True)
class LockAcquireResult:
    """``token`` set when this process owns the lock; ``redis_ok`` False = fail-open."""

    token: str | None
    redis_ok: bool


class DistributedSingleFlight:
    """Cross-process scrape coalescing with TTL-bound ownership tokens."""

    def __init__(
        self,
        gateway: RedisGateway,
        *,
        lock_ttl_seconds: int = 180,
        wait_seconds: float = 120.0,
        poll_min_seconds: float = 0.1,
        poll_max_seconds: float = 0.3,
    ) -> None:
        self._gateway = gateway
        self._lock_ttl_seconds = max(1, int(lock_ttl_seconds))
        self._wait_seconds = max(0.0, float(wait_seconds))
        self._poll_min = max(0.01, float(poll_min_seconds))
        self._poll_max = max(self._poll_min, float(poll_max_seconds))

    def try_acquire(self, url: str, *, quiet: bool = False) -> LockAcquireResult:
        """Try to acquire the flight lock for ``url``.

        ``quiet=True`` suppresses the follower INFO log — used by the wait-loop
        poll (every ~100–300ms) so Docker logs are not flooded while a leader
        scrapes.
        """
        key = flight_lock_key(url)
        token = uuid.uuid4().hex
        ttl_ms = self._lock_ttl_seconds * 1000
        client = self._gateway.get_client()
        if client is None:
            return LockAcquireResult(token=None, redis_ok=False)
        try:
            ok = client.set(key, token.encode("utf-8"), nx=True, px=ttl_ms)
        except RedisError:
            logger.warning(
                "redis_operation_failed", extra={"op": "flight_acquire"}, exc_info=True
            )
            self._gateway.reset()
            return LockAcquireResult(token=None, redis_ok=False)
        if ok:
            logger.info(
                "singleflight_leader",
                extra={
                    "event": "singleflight_leader",
                    "lock_ttl": self._lock_ttl_seconds,
                },
            )
            return LockAcquireResult(token=token, redis_ok=True)
        # First transition to follower stays at INFO via distributed_lock_wait;
        # subsequent poll retries use quiet=True → DEBUG only.
        log = logger.debug if quiet else logger.info
        log(
            "singleflight_follower",
            extra={"event": "singleflight_follower"},
        )
        return LockAcquireResult(token=None, redis_ok=True)

    def release(self, url: str, token: str) -> None:
        key = flight_lock_key(url)
        client = self._gateway.get_client()
        if client is None:
            return
        try:
            client.eval(_RELEASE_LUA, 1, key, token.encode("utf-8"))
        except RedisError:
            logger.warning(
                "redis_operation_failed", extra={"op": "flight_release"}, exc_info=True
            )
            self._gateway.reset()

    @property
    def lock_ttl_seconds(self) -> int:
        return self._lock_ttl_seconds

    def run(
        self,
        url: str,
        fn: Callable[[], T],
        *,
        poll_result: Callable[[], T | None],
    ) -> T:
        """Leader runs ``fn``; followers wait on cache then may steal the lock."""
        acquired = self.try_acquire(url)
        if not acquired.redis_ok:
            return fn()
        if acquired.token is not None:
            try:
                return fn()
            finally:
                self.release(url, acquired.token)

        deadline = time.monotonic() + self._wait_seconds
        logger.info(
            "distributed_lock_wait",
            extra={
                "event": "distributed_lock_wait",
                "wait_seconds": self._wait_seconds,
            },
        )
        while time.monotonic() < deadline:
            cached = poll_result()
            if cached is not None:
                logger.info(
                    "singleflight_follower_resolved",
                    extra={"event": "singleflight_follower_resolved"},
                )
                return cached
            # quiet: do not INFO-spam on every poll while the leader holds the lock
            stolen = self.try_acquire(url, quiet=True)
            if not stolen.redis_ok:
                return fn()
            if stolen.token is not None:
                try:
                    return fn()
                finally:
                    self.release(url, stolen.token)
            time.sleep(random.uniform(self._poll_min, self._poll_max))

        logger.info(
            "distributed_lock_timeout",
            extra={"event": "distributed_lock_timeout"},
        )
        return fn()


class NullDistributedSingleFlight:
    """No-op distributed flight used when Redis coordination is disabled."""

    def run(
        self,
        url: str,
        fn: Callable[[], T],
        *,
        poll_result: Callable[[], T | None],
    ) -> T:
        del url, poll_result
        return fn()
