"""Distributed URL/domain cooldown via Redis SET NX + PTTL (fail-open to local)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from redis.exceptions import RedisError

from .redis_client import RedisGateway
from .redis_keys import domain_cooldown_key, store_cooldown_key, url_cooldown_key

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CooldownAcquireResult:
    """Outcome of a distributed cooldown reservation attempt."""

    allowed: bool
    redis_ok: bool
    retry_after: int | None = None
    code: str | None = None  # DUPLICATE_REQUEST | RATE_LIMITED


class DistributedCooldown:
    """Atomic per-URL and per-domain spacing shared across API instances."""

    def __init__(self, gateway: RedisGateway) -> None:
        self._gateway = gateway

    def try_acquire(
        self,
        url: str,
        hostname: str,
        *,
        url_cooldown_seconds: int,
        domain_min_interval_seconds: float,
    ) -> CooldownAcquireResult:
        """Reserve URL + domain for a live fetch.

        Uses SET NX so reservation happens *before* the upstream request.
        If the domain reservation fails after URL succeeded, the URL key is
        released so a blocked domain burst does not strand the URL cooldown.
        """
        url_ttl = max(0, int(url_cooldown_seconds))
        domain_ttl = max(0, int(domain_min_interval_seconds))
        url_key = url_cooldown_key(url)
        domain_key = domain_cooldown_key(hostname) if hostname else None

        client = self._gateway.get_client()
        if client is None:
            return CooldownAcquireResult(allowed=True, redis_ok=False)

        try:
            if url_ttl > 0:
                ok = client.set(url_key, b"1", nx=True, ex=url_ttl)
                if not ok:
                    pttl = client.pttl(url_key)
                    retry = _retry_after_from_pttl(pttl, fallback=url_ttl)
                    logger.info(
                        "cooldown_url_hit",
                        extra={"event": "cooldown_url_hit", "retry_after": retry},
                    )
                    return CooldownAcquireResult(
                        allowed=False,
                        redis_ok=True,
                        retry_after=retry,
                        code="DUPLICATE_REQUEST",
                    )

            if domain_key and domain_ttl > 0:
                ok = client.set(domain_key, b"1", nx=True, ex=domain_ttl)
                if not ok:
                    if url_ttl > 0:
                        client.delete(url_key)
                    pttl = client.pttl(domain_key)
                    retry = _retry_after_from_pttl(pttl, fallback=domain_ttl)
                    logger.info(
                        "cooldown_domain_hit",
                        extra={
                            "event": "cooldown_domain_hit",
                            "retry_after": retry,
                        },
                    )
                    return CooldownAcquireResult(
                        allowed=False,
                        redis_ok=True,
                        retry_after=retry,
                        code="RATE_LIMITED",
                    )

            return CooldownAcquireResult(allowed=True, redis_ok=True)
        except RedisError:
            logger.warning(
                "redis_operation_failed",
                extra={"op": "cooldown_acquire"},
                exc_info=True,
            )
            self._gateway.reset()
            return CooldownAcquireResult(allowed=True, redis_ok=False)

    def release(self, url: str, hostname: str, *, release_domain: bool) -> None:
        """Free reservations after a failed fetch (best effort, fail-open)."""
        client = self._gateway.get_client()
        if client is None:
            return
        try:
            client.delete(url_cooldown_key(url))
            if release_domain and hostname:
                client.delete(domain_cooldown_key(hostname))
        except RedisError:
            logger.warning(
                "redis_operation_failed",
                extra={"op": "cooldown_release"},
                exc_info=True,
            )
            self._gateway.reset()

    def set_store_cooldown(self, hostname: str, seconds: int) -> None:
        """Pause a store host after classified upstream failures (shared)."""
        client = self._gateway.get_client()
        if client is None or not hostname or seconds <= 0:
            return
        try:
            client.set(store_cooldown_key(hostname), b"1", ex=int(seconds))
        except RedisError:
            logger.warning(
                "redis_operation_failed",
                extra={"op": "store_cooldown_set"},
                exc_info=True,
            )
            self._gateway.reset()

    def store_cooldown_remaining(self, hostname: str) -> int | None:
        """Seconds left of a store pause, 0 if none, None if Redis is down."""
        client = self._gateway.get_client()
        if client is None or not hostname:
            return None
        try:
            pttl = client.pttl(store_cooldown_key(hostname))
        except RedisError:
            logger.warning(
                "redis_operation_failed",
                extra={"op": "store_cooldown_get"},
                exc_info=True,
            )
            self._gateway.reset()
            return None
        if isinstance(pttl, int) and pttl > 0:
            return max(1, (pttl + 999) // 1000)
        return 0


def _retry_after_from_pttl(pttl: object, *, fallback: int) -> int:
    if isinstance(pttl, int) and pttl > 0:
        return max(1, (pttl + 999) // 1000)
    return max(1, int(fallback))
