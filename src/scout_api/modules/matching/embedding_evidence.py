"""Fail-open semantic evidence for inconclusive durable match runs."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Protocol

from redis import Redis
from redis.exceptions import RedisError

from scout_api.core.config import Settings
from scout_api.modules.matching.embedding_provider import (
    EmbeddingProvider,
    EmbeddingProviderError,
    OpenAIEmbeddingProvider,
    cosine_similarity,
)
from scout_api.modules.matching.embedding_text import build_embedding_texts
from scout_api.modules.matching.engine import MatchScore, price_extreme
from scout_api.modules.matching.identity import ProductIdentity
from scout_api.modules.matching.match_deadlines import MonotonicDeadline
from scout_api.modules.matching.schemas import MatchReason

logger = logging.getLogger(__name__)
EMBEDDING_TEXT_VERSION = "embedding-text-v1"


def embedding_eligible(score: MatchScore) -> bool:
    """Semantic calls are reserved for uncertainty explicitly named by matcher."""
    return score.decision == "review" and any(
        reason.code == "variant_semantic_uncertain" for reason in score.reasons
    )


@dataclass(frozen=True)
class EmbeddingEvidenceResult:
    status: str
    provider: str
    model: str
    representation: str
    similarity: float | None = None
    error_code: str | None = None
    duration_ms: int = 0
    cache_hits: int = 0
    input_tokens: int | None = None

    def as_json(self) -> dict[str, str | float | int | None]:
        return {
            "status": self.status,
            "provider": self.provider,
            "model": self.model,
            "representation": self.representation,
            "similarity": self.similarity,
            "error_code": self.error_code,
            "duration_ms": self.duration_ms,
            "cache_hits": self.cache_hits,
            "input_tokens": self.input_tokens,
        }


def apply_embedding_evidence(
    score: MatchScore,
    evidence: EmbeddingEvidenceResult,
    *,
    mode: str,
    active_threshold: float | None,
    reference: ProductIdentity | None = None,
    candidate: ProductIdentity | None = None,
) -> MatchScore:
    """Allow only a narrow exact-identity variant uncertainty promotion."""
    reasons = {reason.code for reason in score.reasons}
    if (
        mode != "active"
        or not embedding_eligible(score)
        or evidence.status != "ok"
        or evidence.similarity is None
        or active_threshold is None
        or evidence.similarity < active_threshold
        or "brand_model_exact" not in reasons
        or "variant_semantic_uncertain" not in reasons
        or "price_deviation" in reasons
        or (
            reference is not None
            and candidate is not None
            and price_extreme(reference, candidate)
        )
    ):
        return score
    return replace(
        score,
        decision="auto_match",
        confidence=max(score.confidence, Decimal("0.9000")),
        reasons=(
            *score.reasons,
            MatchReason(
                code="embedding_variant_support",
                detail=f"cosine_similarity={evidence.similarity:.6f}",
                score=evidence.similarity,
            ),
        ),
    )


class EmbeddingCache:
    """Bounded worker-process LRU; never uses Redis or persistent storage."""

    def __init__(self, *, max_entries: int, ttl_seconds: int) -> None:
        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds
        self._values: OrderedDict[str, tuple[float, tuple[float, ...]]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> tuple[float, ...] | None:
        now = time.monotonic()
        with self._lock:
            entry = self._values.get(key)
            if entry is None:
                return None
            created, vector = entry
            if now - created >= self._ttl_seconds:
                del self._values[key]
                return None
            self._values.move_to_end(key)
            return vector

    def put(self, key: str, vector: tuple[float, ...]) -> None:
        with self._lock:
            self._values[key] = (time.monotonic(), vector)
            self._values.move_to_end(key)
            while len(self._values) > self._max_entries:
                self._values.popitem(last=False)


class EmbeddingVectorCache(Protocol):
    def get(self, key: str) -> tuple[float, ...] | None: ...

    def put(self, key: str, vector: tuple[float, ...]) -> None: ...


class RedisEmbeddingCache:
    """Redis L2 with a bounded process-local L1; keys contain only SHA-256."""

    _KEY_PREFIX = "scout:match:embedding:v1:"

    def __init__(
        self,
        *,
        max_entries: int,
        ttl_seconds: int,
        redis_url: str,
        client: Redis | None = None,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._local = EmbeddingCache(max_entries=max_entries, ttl_seconds=ttl_seconds)
        self._client = client or Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=0.2,
            socket_timeout=0.2,
        )

    def get(self, key: str) -> tuple[float, ...] | None:
        local = self._local.get(key)
        if local is not None:
            return local
        redis_key = f"{self._KEY_PREFIX}{key}"
        try:
            encoded = self._client.get(redis_key)
        except RedisError:
            logger.warning("match_embedding_cache_redis_read_failed")
            return None
        if encoded is None:
            return None
        if not isinstance(encoded, (str, bytes, bytearray)):
            return None
        try:
            values = json.loads(encoded)
            if not isinstance(values, list) or not values:
                raise ValueError("invalid cached vector")
            if any(isinstance(value, bool) for value in values):
                raise ValueError("invalid cached vector")
            vector = tuple(float(value) for value in values)
            if any(not math.isfinite(value) for value in vector):
                raise ValueError("invalid cached vector")
        except (TypeError, ValueError, json.JSONDecodeError):
            try:
                self._client.delete(redis_key)
            except RedisError:
                pass
            return None
        self._local.put(key, vector)
        return vector

    def put(self, key: str, vector: tuple[float, ...]) -> None:
        self._local.put(key, vector)
        try:
            self._client.setex(
                f"{self._KEY_PREFIX}{key}",
                self._ttl_seconds,
                json.dumps(vector, separators=(",", ":")),
            )
        except RedisError:
            logger.warning("match_embedding_cache_redis_write_failed")

    def close(self) -> None:
        self._client.close()


class MatchEmbeddingEvaluator:
    """Per-run provider calls and metadata, backed by a shared bounded cache."""

    def __init__(
        self,
        *,
        settings: Settings,
        provider: EmbeddingProvider,
        cache: EmbeddingVectorCache,
    ) -> None:
        self._settings = settings
        self._provider = provider
        self._cache = cache
        self._calls = 0
        self._lock = threading.Lock()

    def new_run(self) -> MatchEmbeddingEvaluator:
        return MatchEmbeddingEvaluator(
            settings=self._settings, provider=self._provider, cache=self._cache
        )

    @classmethod
    def create(
        cls,
        settings: Settings,
        *,
        provider: EmbeddingProvider | None = None,
        cache: EmbeddingVectorCache | None = None,
    ) -> MatchEmbeddingEvaluator:
        actual_provider = provider
        if actual_provider is None:
            if settings.match_embeddings_provider not in {
                "openai",
                "openai_compatible",
            }:
                raise ValueError("provider de embeddings indisponível")
            if (
                settings.match_embeddings_provider == "openai"
                and not settings.match_embeddings_api_key
            ):
                raise ValueError("provider de embeddings indisponível")
            actual_provider = OpenAIEmbeddingProvider(
                api_key=settings.match_embeddings_api_key or "",
                api_url=settings.match_embeddings_api,
                provider_name=settings.match_embeddings_provider,
                model=settings.match_embeddings_model,
                cache_revision=settings.match_embeddings_cache_revision,
                dimensions=settings.match_embeddings_dimensions,
                input_prefix=settings.match_embeddings_input_prefix,
                max_batch_size=settings.match_embeddings_batch_size,
                timeout_seconds=settings.match_embeddings_timeout_seconds,
            )
        actual_cache = cache
        if actual_cache is None:
            cache_options = {
                "max_entries": settings.match_embeddings_cache_entries,
                "ttl_seconds": settings.match_embeddings_cache_ttl_seconds,
            }
            if settings.match_embeddings_cache_backend == "redis":
                if settings.redis_url:
                    actual_cache = RedisEmbeddingCache(
                        max_entries=settings.match_embeddings_cache_entries,
                        ttl_seconds=settings.match_embeddings_cache_ttl_seconds,
                        redis_url=settings.redis_url,
                    )
                else:
                    logger.warning(
                        "match_embedding_cache_redis_url_missing_local_fallback"
                    )
                    actual_cache = EmbeddingCache(**cache_options)
            else:
                actual_cache = EmbeddingCache(**cache_options)
        return cls(settings=settings, provider=actual_provider, cache=actual_cache)

    def evaluate(
        self,
        reference: ProductIdentity,
        candidate: ProductIdentity,
        *,
        deadline: MonotonicDeadline | None = None,
    ) -> EmbeddingEvidenceResult:
        started = time.perf_counter()
        cache_hits = 0
        info = None
        representation = self._settings.match_embeddings_representation
        input_tokens: int | None = 0
        try:
            info = self._provider.model_info
            with self._lock:
                if (
                    self._calls
                    >= self._settings.match_embeddings_max_candidates_per_run
                ):
                    return EmbeddingEvidenceResult(
                        "skipped_limit", info.provider, info.model, representation
                    )
                self._calls += 1
            if deadline is not None:
                remaining = deadline.remaining_seconds()
                if (
                    remaining is not None
                    and remaining <= self._settings.match_embeddings_timeout_seconds
                ):
                    return EmbeddingEvidenceResult(
                        "skipped_deadline", info.provider, info.model, representation
                    )
            ref_text = build_embedding_texts(reference).get(representation)
            cand_text = build_embedding_texts(candidate).get(representation)
            if not ref_text or not cand_text:
                return EmbeddingEvidenceResult(
                    "skipped_empty", info.provider, info.model, representation
                )
            ref_key = self._cache_key(
                info.provider,
                info.model_version,
                info.dimensions,
                representation,
                ref_text,
            )
            cand_key = self._cache_key(
                info.provider,
                info.model_version,
                info.dimensions,
                representation,
                cand_text,
            )
            vectors: dict[str, tuple[float, ...]] = {}
            missing: list[tuple[str, str]] = []
            for key, text in ((ref_key, ref_text), (cand_key, cand_text)):
                cached = vectors.get(key) or self._cache.get(key)
                if cached is not None:
                    vectors[key] = cached
                    cache_hits += 1
                elif all(key != pending_key for pending_key, _ in missing):
                    missing.append((key, text))
            if missing:
                batch_with_usage = getattr(
                    self._provider, "embed_batch_with_usage", None
                )
                if callable(batch_with_usage):
                    generated, input_tokens = batch_with_usage(
                        [text for _, text in missing]
                    )
                else:
                    generated = self._provider.embed_batch(
                        [text for _, text in missing]
                    )
                    input_tokens = None
                if len(generated) != len(missing):
                    raise EmbeddingProviderError("invalid_batch_response")
                for (key, _), vector in zip(missing, generated, strict=True):
                    vectors[key] = vector
                    self._cache.put(key, vector)
            similarity = cosine_similarity(vectors[ref_key], vectors[cand_key])
            return EmbeddingEvidenceResult(
                "ok",
                info.provider,
                info.model,
                representation,
                round(similarity, 6),
                duration_ms=int((time.perf_counter() - started) * 1000),
                cache_hits=cache_hits,
                input_tokens=input_tokens,
            )
        except EmbeddingProviderError as exc:
            logger.warning("match_embedding_provider_failed code=%s", exc.code)
            return EmbeddingEvidenceResult(
                "error",
                info.provider if info else "unknown",
                info.model if info else "unknown",
                representation,
                error_code=exc.code,
                duration_ms=int((time.perf_counter() - started) * 1000),
                cache_hits=cache_hits,
                input_tokens=input_tokens,
            )
        except Exception:  # noqa: BLE001 - semantic evidence must fail open
            logger.exception("match_embedding_evaluation_failed")
            return EmbeddingEvidenceResult(
                "error",
                info.provider if info else "unknown",
                info.model if info else "unknown",
                representation,
                error_code="internal_error",
                duration_ms=int((time.perf_counter() - started) * 1000),
                cache_hits=cache_hits,
                input_tokens=input_tokens,
            )

    @staticmethod
    def _cache_key(
        provider: str,
        version: str,
        dimensions: int,
        representation: str,
        text: str,
    ) -> str:
        payload = json.dumps(
            [
                EMBEDDING_TEXT_VERSION,
                provider,
                version,
                dimensions,
                representation,
                text,
            ],
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class MatchEmbeddingRuntime:
    """Long-lived provider and cache shared by runs in the dedicated worker."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._base = MatchEmbeddingEvaluator.create(settings)

    def new_run(self) -> MatchEmbeddingEvaluator:
        return self._base.new_run()

    def close(self) -> None:
        close = getattr(self._base._provider, "close", None)
        if callable(close):
            close()
        cache_close = getattr(self._base._cache, "close", None)
        if callable(cache_close):
            cache_close()
