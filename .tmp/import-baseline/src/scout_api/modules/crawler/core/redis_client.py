"""Lazy Redis client factory with fail-open helpers (no import-time connect)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, TypeVar
from urllib.parse import urlsplit, urlunsplit

from redis import Redis
from redis.exceptions import RedisError

from ....core.config import Settings

logger = logging.getLogger(__name__)

T = TypeVar("T")


def redact_redis_url(url: str) -> str:
    """Strip credentials from a Redis URL for safe logging."""
    parts = urlsplit(url)
    if not parts.password and "@" not in (parts.netloc or ""):
        return f"{parts.scheme}://{parts.hostname or 'unknown'}:{parts.port or 6379}"
    host = parts.hostname or "unknown"
    port = parts.port or 6379
    user = parts.username or ""
    auth = f"{user}:***@" if user else "***@"
    return urlunsplit((parts.scheme, f"{auth}{host}:{port}", parts.path, "", ""))


def redis_endpoint_meta(url: str) -> dict[str, str | int | None]:
    parts = urlsplit(url)
    db = 0
    path = (parts.path or "").lstrip("/")
    if path.isdigit():
        db = int(path)
    return {
        "redis_host": parts.hostname,
        "redis_port": parts.port or 6379,
        "redis_db": db,
    }


class RedisGateway:
    """On-demand Redis connection with short timeouts and fail-open ops."""

    def __init__(
        self,
        url: str,
        *,
        connect_timeout: float = 0.3,
        socket_timeout: float = 0.5,
        client: Any | None = None,
    ) -> None:
        self._url = url
        self._connect_timeout = connect_timeout
        self._socket_timeout = socket_timeout
        self._client: Any | None = client
        self._injected = client is not None

    @property
    def url(self) -> str:
        return self._url

    def get_client(self) -> Any | None:
        if self._injected:
            return self._client
        if self._client is not None:
            return self._client
        try:
            client = Redis.from_url(
                self._url,
                decode_responses=False,
                socket_connect_timeout=self._connect_timeout,
                socket_timeout=self._socket_timeout,
                health_check_interval=30,
            )
            # Cheap ping to fail fast on misconfig; still fail-open for callers.
            client.ping()
            self._client = client
            logger.info(
                "redis_connected",
                extra=redis_endpoint_meta(self._url),
            )
            return self._client
        except RedisError:
            logger.warning(
                "redis_unavailable",
                extra={
                    **redis_endpoint_meta(self._url),
                    "redis_url_redacted": redact_redis_url(self._url),
                },
                exc_info=True,
            )
            self._client = None
            return None

    def reset(self) -> None:
        """Drop the cached client (e.g. after a connection error)."""
        if self._injected:
            return
        client = self._client
        self._client = None
        if client is not None:
            try:
                client.close()
            except RedisError:
                pass

    def execute(self, operation: Callable[[Any], T]) -> T | None:
        client = self.get_client()
        if client is None:
            return None
        try:
            return operation(client)
        except RedisError:
            logger.warning(
                "redis_operation_failed",
                extra=redis_endpoint_meta(self._url),
                exc_info=True,
            )
            self.reset()
            return None


def build_redis_gateway(settings: Settings) -> RedisGateway | None:
    """Return a gateway when REDIS_URL is set; never connect at import time."""
    url = (settings.redis_url or "").strip()
    if not url:
        return None
    return RedisGateway(
        url,
        connect_timeout=settings.redis_connect_timeout_seconds,
        socket_timeout=settings.redis_socket_timeout_seconds,
    )
