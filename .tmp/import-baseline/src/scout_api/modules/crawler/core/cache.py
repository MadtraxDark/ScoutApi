"""Scrape result cache: typed façade over byte CacheBackends (L1 memory + L2 Redis)."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypeVar

from ..models.product import ProductOffer, ProductPriceItem
from .redis_client import RedisGateway
from .redis_keys import scrape_cache_key

logger = logging.getLogger(__name__)

T = TypeVar("T")

SCHEMA_VERSION = 1
KIND_PRODUCT = "product_price_item"
KIND_OFFER = "product_offer"

CacheValue = ProductPriceItem | ProductOffer
ServedFrom = Literal["memory", "redis"]


class CacheBackend(Protocol):
    def get(self, key: str) -> bytes | None: ...

    def set(self, key: str, value: bytes, ttl: int) -> None: ...

    def delete(self, key: str) -> None: ...


@dataclass(frozen=True)
class CacheLookup:
    value: CacheValue
    served_from: ServedFrom


@dataclass
class _MemoryEntry:
    value: bytes
    expires_at: float


class InMemoryCacheBackend:
    """Process-local byte cache (L1 or standalone fallback)."""

    def __init__(self) -> None:
        self._entries: dict[str, _MemoryEntry] = {}

    def get(self, key: str) -> bytes | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.expires_at <= time.monotonic():
            self._entries.pop(key, None)
            return None
        return entry.value

    def set(self, key: str, value: bytes, ttl: int) -> None:
        ttl = max(0, int(ttl))
        if ttl <= 0:
            self._entries.pop(key, None)
            return
        self._entries[key] = _MemoryEntry(value, time.monotonic() + ttl)

    def delete(self, key: str) -> None:
        self._entries.pop(key, None)

    def get_ttl(self, key: str) -> int | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        remaining = int(entry.expires_at - time.monotonic())
        if remaining <= 0:
            self._entries.pop(key, None)
            return None
        return remaining


class RedisCacheBackend:
    """Distributed byte cache; all ops fail-open (log + act as miss/no-op)."""

    def __init__(self, gateway: RedisGateway) -> None:
        self._gateway = gateway

    def get(self, key: str) -> bytes | None:
        def _op(client: Any) -> bytes | None:
            raw = client.get(key)
            if raw is None:
                return None
            if isinstance(raw, str):
                return raw.encode("utf-8")
            return bytes(raw)

        return self._gateway.execute(_op)

    def get_with_ttl(self, key: str) -> tuple[bytes, int] | None:
        def _op(client: Any) -> tuple[bytes, int] | None:
            pipe = client.pipeline()
            pipe.get(key)
            pipe.pttl(key)
            raw, pttl = pipe.execute()
            if raw is None:
                return None
            if isinstance(raw, str):
                raw_bytes = raw.encode("utf-8")
            else:
                raw_bytes = bytes(raw)
            if pttl is None or pttl < 0:
                # -1 = no expiry; treat as at least 1s for L1 populate
                ttl_seconds = 1 if pttl == -1 else 0
            else:
                ttl_seconds = max(1, (int(pttl) + 999) // 1000)
            if ttl_seconds <= 0:
                return None
            return raw_bytes, ttl_seconds

        return self._gateway.execute(_op)

    def set(self, key: str, value: bytes, ttl: int) -> None:
        ttl = max(0, int(ttl))
        if ttl <= 0:
            return

        def _op(client: Any) -> bool:
            client.set(key, value, ex=ttl)
            return True

        self._gateway.execute(_op)

    def delete(self, key: str) -> None:
        def _op(client: Any) -> bool:
            client.delete(key)
            return True

        self._gateway.execute(_op)


class HybridCacheBackend:
    """L1 in-process memory + L2 Redis; Redis errors degrade to L1-only."""

    def __init__(self, l1: InMemoryCacheBackend, l2: RedisCacheBackend) -> None:
        self._l1 = l1
        self._l2 = l2

    def get(self, key: str) -> bytes | None:
        hit = self._l1.get(key)
        if hit is not None:
            return hit
        entry = self._l2.get_with_ttl(key)
        if entry is None:
            return None
        value, ttl = entry
        self._l1.set(key, value, ttl)
        return value

    def get_with_source(self, key: str) -> tuple[bytes, ServedFrom] | None:
        hit = self._l1.get(key)
        if hit is not None:
            return hit, "memory"
        entry = self._l2.get_with_ttl(key)
        if entry is None:
            return None
        value, ttl = entry
        self._l1.set(key, value, ttl)
        return value, "redis"

    def set(self, key: str, value: bytes, ttl: int) -> None:
        self._l1.set(key, value, ttl)
        self._l2.set(key, value, ttl)

    def delete(self, key: str) -> None:
        self._l1.delete(key)
        self._l2.delete(key)


def serialize_cache_value(value: CacheValue) -> bytes:
    if isinstance(value, ProductPriceItem):
        kind = KIND_PRODUCT
    elif isinstance(value, ProductOffer):
        kind = KIND_OFFER
    else:
        raise TypeError(f"unsupported cache value type: {type(value)!r}")
    envelope = {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "payload": value.model_dump(mode="json"),
    }
    return json.dumps(envelope, separators=(",", ":")).encode("utf-8")


def deserialize_cache_value(raw: bytes) -> CacheValue | None:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        return None
    if not isinstance(data, dict):
        return None
    version = data.get("schema_version")
    kind = data.get("kind")
    payload = data.get("payload")
    if version != SCHEMA_VERSION or not isinstance(payload, dict):
        return None
    try:
        if kind == KIND_PRODUCT:
            return ProductPriceItem.model_validate(payload)
        if kind == KIND_OFFER:
            return ProductOffer.model_validate(payload)
    except Exception:
        logger.warning("cache_deserialize_failed", extra={"kind": kind}, exc_info=True)
        return None
    return None


class ResponseCache:
    """High-level scrape cache preserving ProductPriceItem / ProductOffer semantics."""

    def __init__(self, backend: CacheBackend | None = None) -> None:
        self._backend: CacheBackend = backend or InMemoryCacheBackend()

    @property
    def backend(self) -> CacheBackend:
        return self._backend

    def lookup(self, key: str) -> CacheLookup | None:
        served_from: ServedFrom = "memory"
        raw: bytes | None
        if isinstance(self._backend, HybridCacheBackend):
            sourced = self._backend.get_with_source(key)
            if sourced is None:
                _emit("cache_miss", backend=_backend_name(self._backend))
                return None
            raw, served_from = sourced
        else:
            raw = self._backend.get(key)
            if raw is None:
                _emit("cache_miss", backend=_backend_name(self._backend))
                return None
            if isinstance(self._backend, RedisCacheBackend):
                served_from = "redis"

        value = deserialize_cache_value(raw)
        if value is None:
            _emit(
                "cache_error",
                backend=_backend_name(self._backend),
                reason="invalid_payload",
            )
            self._backend.delete(key)
            return None

        _emit(
            "cache_hit",
            backend=_backend_name(self._backend),
            served_from=served_from,
        )
        return CacheLookup(value=value, served_from=served_from)

    def get(self, key: str) -> CacheValue | None:
        hit = self.lookup(key)
        return hit.value if hit is not None else None

    def set(self, key: str, value: CacheValue, ttl: int) -> None:
        try:
            payload = serialize_cache_value(value)
        except TypeError:
            logger.warning(
                "cache_serialize_rejected",
                extra={"type": type(value).__name__},
            )
            return
        self._backend.set(key, payload, ttl)

    def delete(self, key: str) -> None:
        self._backend.delete(key)


def build_cache_backend(
    *,
    redis_gateway: RedisGateway | None = None,
) -> CacheBackend:
    """Factory: Redis URL present → Hybrid L1+L2; else in-memory only.

    Does not connect to Redis at call time when using a lazy gateway.
    """
    memory = InMemoryCacheBackend()
    if redis_gateway is None:
        return memory
    return HybridCacheBackend(memory, RedisCacheBackend(redis_gateway))


def cache_key_for_url(url: str) -> str:
    """Public helper: versioned Redis/memory key for a product URL."""
    return scrape_cache_key(url)


def _backend_name(backend: CacheBackend) -> str:
    if isinstance(backend, HybridCacheBackend):
        return "hybrid"
    if isinstance(backend, RedisCacheBackend):
        return "redis"
    if isinstance(backend, InMemoryCacheBackend):
        return "memory"
    return type(backend).__name__


def _emit(event: str, **fields: Any) -> None:
    # cache_miss is polled every ~100–300ms by distributed single-flight
    # followers; keep it at DEBUG so INFO logs stay actionable.
    if event in {"cache_miss", "cache_hit"}:
        logger.debug(event, extra=fields)
        return
    logger.info(event, extra=fields)


# Backward-compatible alias used by older imports/tests.
CacheEntry = _MemoryEntry
