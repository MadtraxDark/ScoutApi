from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from scout_api.core.config import Settings
from scout_api.modules.matching.embedding_evidence import (
    EmbeddingCache,
    EmbeddingEvidenceResult,
    MatchEmbeddingEvaluator,
    RedisEmbeddingCache,
    apply_embedding_evidence,
    embedding_eligible,
)
from scout_api.modules.matching.embedding_provider import (
    EmbeddingModelInfo,
    EmbeddingProviderError,
    OpenAIEmbeddingProvider,
)
from scout_api.modules.matching.engine import MatchScore
from scout_api.modules.matching.identity import ProductIdentity
from scout_api.modules.matching.schemas import MatchReason


class FakeProvider:
    model_info = EmbeddingModelInfo("fake", "test-model", "rev-1", 2)

    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    def embed_batch(self, texts: list[str]) -> list[tuple[float, ...]]:
        self.batches.append(list(texts))
        return [(1.0, 0.0) for _ in texts]


class BrokenMetadataProvider:
    @property
    def model_info(self) -> EmbeddingModelInfo:
        raise RuntimeError("provider not ready")

    def embed_batch(self, _texts: list[str]) -> list[tuple[float, ...]]:
        raise AssertionError("must not request vectors")


class BrokenEmbeddingProvider(FakeProvider):
    def embed_batch(self, _texts: list[str]) -> list[tuple[float, ...]]:
        raise EmbeddingProviderError("rate_limit", retryable=True)


class FakeRedisCacheClient:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.reads = 0

    def get(self, key: str) -> str | None:
        self.reads += 1
        return self.values.get(key)

    def setex(self, key: str, _ttl: int, value: str) -> None:
        self.values[key] = value

    def delete(self, key: str) -> None:
        self.values.pop(key, None)

    def close(self) -> None:
        return None


class BrokenRedisCacheClient(FakeRedisCacheClient):
    def get(self, _key: str) -> None:
        raise RedisConnectionError("offline")

    def setex(self, _key: str, _ttl: int, _value: str) -> None:
        raise RedisConnectionError("offline")


def _identity(title: str, *, price: Decimal | None = None) -> ProductIdentity:
    return ProductIdentity(
        gtin=None,
        brand="Acme",
        model="Model 10",
        title=title,
        title_normalized=title.casefold(),
        variant_attrs={"color": "coral"},
        price=price,
        currency="USD" if price is not None else None,
    )


def _review_score(*codes: str) -> MatchScore:
    return MatchScore(
        decision="review",
        confidence=Decimal("0.8500"),
        reasons=tuple(MatchReason(code=code, detail="", score=0.5) for code in codes),
    )


def test_evaluator_batches_pair_and_reuses_cached_reference() -> None:
    settings = Settings(
        match_embeddings_mode="shadow", match_embeddings_representation="raw"
    )
    provider = FakeProvider()
    cache = EmbeddingCache(max_entries=8, ttl_seconds=60)
    evaluator = MatchEmbeddingEvaluator.create(settings, provider=provider, cache=cache)

    first = evaluator.evaluate(
        _identity("Acme Model 10 Coral"), _identity("Acme Model 10 Coral A")
    )
    second = evaluator.evaluate(
        _identity("Acme Model 10 Coral"), _identity("Acme Model 10 Coral B")
    )

    assert first.status == second.status == "ok"
    assert first.input_tokens is None  # Fake provider does not report usage.
    assert len(provider.batches) == 2
    assert len(provider.batches[0]) == 2
    assert len(provider.batches[1]) == 1
    cached = evaluator.evaluate(
        _identity("Acme Model 10 Coral"), _identity("Acme Model 10 Coral A")
    )
    assert cached.cache_hits == 2
    assert cached.input_tokens == 0


def test_embedding_cache_key_scopes_version_representation_and_input() -> None:
    cache_key = MatchEmbeddingEvaluator._cache_key
    baseline = cache_key("openai", "model-v1", 1536, "raw", "product title")

    variants = (
        cache_key("other-provider", "model-v1", 1536, "raw", "product title"),
        cache_key("openai", "model-v2", 1536, "raw", "product title"),
        cache_key("openai", "model-v1", 3072, "raw", "product title"),
        cache_key("openai", "model-v1", 1536, "hybrid", "product title"),
        cache_key("openai", "model-v1", 1536, "raw", "updated product title"),
    )

    assert len(set((baseline, *variants))) == 1 + len(variants)


def test_embedding_cache_expires_entries_after_ttl() -> None:
    cache = EmbeddingCache(max_entries=2, ttl_seconds=0)
    cache.put("expired", (0.5, 0.5))

    assert cache.get("expired") is None


def test_redis_embedding_cache_shares_vectors_between_local_caches() -> None:
    redis_client = FakeRedisCacheClient()
    first = RedisEmbeddingCache(
        max_entries=2,
        ttl_seconds=60,
        redis_url="redis://unused",
        client=redis_client,  # type: ignore[arg-type]
    )
    second = RedisEmbeddingCache(
        max_entries=2,
        ttl_seconds=60,
        redis_url="redis://unused",
        client=redis_client,  # type: ignore[arg-type]
    )
    first.put("sha256-key", (0.1, 0.2))

    assert second.get("sha256-key") == (0.1, 0.2)
    assert second.get("sha256-key") == (0.1, 0.2)
    assert redis_client.reads == 1
    assert all(
        key.startswith(RedisEmbeddingCache._KEY_PREFIX) for key in redis_client.values
    )


def test_redis_embedding_cache_falls_back_to_process_l1() -> None:
    cache = RedisEmbeddingCache(
        max_entries=2,
        ttl_seconds=60,
        redis_url="redis://unused",
        client=BrokenRedisCacheClient(),  # type: ignore[arg-type]
    )
    cache.put("sha256-key", (0.3, 0.4))

    assert cache.get("sha256-key") == (0.3, 0.4)


def test_provider_metadata_failure_is_fail_open() -> None:
    evaluator = MatchEmbeddingEvaluator.create(
        Settings(_env_file=None, match_embeddings_mode="shadow"),
        provider=BrokenMetadataProvider(),  # type: ignore[arg-type]
    )
    result = evaluator.evaluate(_identity("reference"), _identity("candidate"))
    assert result.status == "error"
    assert result.error_code == "internal_error"


def test_embedding_request_failure_is_fail_open() -> None:
    score = _review_score("brand_model_exact", "variant_semantic_uncertain")
    evaluator = MatchEmbeddingEvaluator.create(
        Settings(_env_file=None, match_embeddings_mode="shadow"),
        provider=BrokenEmbeddingProvider(),
    )

    result = evaluator.evaluate(
        _identity("Acme Model 10 Coral"), _identity("Acme Model 10 Coral A")
    )

    assert result.status == "error"
    assert result.error_code == "rate_limit"
    assert (
        apply_embedding_evidence(score, result, mode="shadow", active_threshold=0.9)
        == score
    )


def test_embedding_call_gate_excludes_clear_and_rejected_decisions() -> None:
    assert embedding_eligible(_review_score("variant_semantic_uncertain"))
    assert not embedding_eligible(_review_score("title_similarity"))
    assert not embedding_eligible(
        MatchScore(
            "reject",
            Decimal("0"),
            (MatchReason(code="variant_mismatch", detail="", score=0),),
        )
    )
    assert not embedding_eligible(
        MatchScore(
            "auto_match",
            Decimal("1"),
            (MatchReason(code="brand_model_exact", detail="", score=1),),
        )
    )


def test_shadow_and_embedding_alone_never_promote_to_auto_match() -> None:
    score = _review_score("brand_model_exact", "variant_semantic_uncertain")
    evidence = EmbeddingEvidenceResult("ok", "fake", "test", "raw", 0.99)

    assert (
        apply_embedding_evidence(score, evidence, mode="shadow", active_threshold=0.9)
        == score
    )
    assert (
        apply_embedding_evidence(
            _review_score("title_similarity"),
            evidence,
            mode="active",
            active_threshold=0.9,
        ).decision
        == "review"
    )


def test_active_promotion_requires_exact_identity_and_no_price_deviation() -> None:
    evidence = EmbeddingEvidenceResult("ok", "fake", "test", "raw", 0.97)
    eligible = _review_score("brand_model_exact", "variant_semantic_uncertain")
    promoted = apply_embedding_evidence(
        eligible,
        evidence,
        mode="active",
        active_threshold=0.95,
        reference=_identity("ref", price=Decimal("100")),
        candidate=_identity("candidate", price=Decimal("450")),
    )
    assert promoted.decision == "review"

    normal_price = apply_embedding_evidence(
        eligible,
        evidence,
        mode="active",
        active_threshold=0.95,
        reference=_identity("ref", price=Decimal("100")),
        candidate=_identity("candidate", price=Decimal("150")),
    )
    assert normal_price.decision == "auto_match"
    assert "embedding_variant_support" in {
        reason.code for reason in normal_price.reasons
    }

    for score in (
        _review_score(
            "brand_model_exact", "variant_semantic_uncertain", "price_deviation"
        ),
        _review_score("variant_semantic_uncertain"),
    ):
        assert (
            apply_embedding_evidence(
                score, evidence, mode="active", active_threshold=0.95
            ).decision
            == "review"
        )


def test_active_promotion_requires_configured_threshold() -> None:
    score = _review_score("brand_model_exact", "variant_semantic_uncertain")
    evidence = EmbeddingEvidenceResult("ok", "fake", "test", "raw", 1.0)
    assert (
        apply_embedding_evidence(
            score, evidence, mode="active", active_threshold=None
        ).decision
        == "review"
    )


def test_active_settings_require_key_and_threshold() -> None:
    with pytest.raises(ValueError, match="API_KEY"):
        Settings(_env_file=None, match_embeddings_mode="active")
    with pytest.raises(ValueError, match="API_KEY"):
        Settings(
            _env_file=None,
            match_embeddings_mode="active",
            match_embeddings_api_key="   ",
        )
    with pytest.raises(ValueError, match="THRESHOLD"):
        Settings(
            _env_file=None,
            match_embeddings_mode="active",
            match_embeddings_api_key="test-key",
        )
    configured = Settings(
        _env_file=None,
        match_embeddings_mode="active",
        match_embeddings_api_key="test-key",
        match_embeddings_active_similarity_threshold="0.95",
    )
    assert configured.match_embeddings_active_similarity_threshold == 0.95


def test_blank_active_threshold_is_none_for_compose_empty_env() -> None:
    configured = Settings(
        _env_file=None,
        match_embeddings_active_similarity_threshold="",
    )
    assert configured.match_embeddings_active_similarity_threshold is None


def test_local_openai_compatible_provider_omits_auth_and_prefixes_inputs() -> None:
    seen: dict[str, object] = {}

    def respond(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("authorization")
        seen["body"] = request.read().decode()
        return httpx.Response(
            200,
            json={"data": [{"index": 0, "embedding": [0.1, 0.2]}]},
        )

    provider = OpenAIEmbeddingProvider(
        api_key="",
        api_url="http://embedding-service:80/v1/embeddings",
        provider_name="openai_compatible",
        model="intfloat/multilingual-e5-small",
        dimensions=2,
        input_prefix="query: ",
        client=httpx.Client(transport=httpx.MockTransport(respond)),
    )

    assert provider.embed_batch(["product title"]) == [(0.1, 0.2)]
    assert seen["url"] == "http://embedding-service/v1/embeddings"
    assert seen["authorization"] is None
    assert '"input":["query: product title"]' in str(seen["body"])


def test_provider_splits_large_input_batches_and_preserves_order() -> None:
    requests: list[list[str]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        inputs = body["input"]
        requests.append(inputs)
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": index, "embedding": [float(index), 1.0]}
                    for index, _ in enumerate(inputs)
                ],
                "usage": {"prompt_tokens": len(inputs)},
            },
        )

    provider = OpenAIEmbeddingProvider(
        api_key="",
        api_url="http://embedding-service/v1/embeddings",
        provider_name="openai_compatible",
        model="local-model",
        dimensions=2,
        max_batch_size=8,
        client=httpx.Client(transport=httpx.MockTransport(respond)),
    )

    vectors, tokens = provider.embed_batch_with_usage([f"item {i}" for i in range(10)])

    assert [len(request) for request in requests] == [8, 2]
    assert len(vectors) == 10
    assert vectors[0] == (0.0, 1.0)
    assert vectors[8] == (0.0, 1.0)
    assert tokens == 10


def test_active_local_provider_needs_threshold_but_no_api_key() -> None:
    configured = Settings(
        _env_file=None,
        match_embeddings_mode="active",
        match_embeddings_provider="openai_compatible",
        match_embeddings_api_key="",
        match_embeddings_active_similarity_threshold=0.95,
    )
    assert configured.match_embeddings_api_key == ""
