from __future__ import annotations

import json

import httpx
import pytest

from scout_api.modules.matching.embedding_provider import (
    EmbeddingProviderError,
    OpenAIEmbeddingProvider,
    cosine_similarity,
)


def _client(handler) -> httpx.Client:
    return httpx.Client(
        base_url="https://api.openai.com/v1/",
        transport=httpx.MockTransport(handler),
    )


def test_openai_provider_batches_and_restores_input_order() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["model"] == "text-embedding-3-small"
        assert payload["dimensions"] == 3
        assert payload["input"] == ["primeiro", "segundo"]
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0, 1, 0]},
                    {"index": 0, "embedding": [1, 0, 0]},
                ],
                "usage": {"prompt_tokens": 42},
            },
        )

    provider = OpenAIEmbeddingProvider(
        api_key="test-key",
        dimensions=3,
        client=_client(handler),
    )

    vectors, prompt_tokens = provider.embed_batch_with_usage(["primeiro", "segundo"])
    assert vectors == [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]
    assert prompt_tokens == 42
    assert provider.model_info.dimensions == 3


def test_embedding_cache_version_scopes_endpoint_and_weight_revision() -> None:
    provider = OpenAIEmbeddingProvider(
        api_key="secret-test-value",
        api_url="https://provider.example/v1/embeddings?token=secret-test-value",
        provider_name="openai_compatible",
        model="shared-model-name",
        dimensions=3,
        cache_revision="weights-2",
    )
    other_endpoint = OpenAIEmbeddingProvider(
        api_key="secret-test-value",
        api_url="https://other-provider.example/v1/embeddings",
        provider_name="openai_compatible",
        model="shared-model-name",
        dimensions=3,
        cache_revision="weights-2",
    )
    other_revision = OpenAIEmbeddingProvider(
        api_key="secret-test-value",
        api_url="https://provider.example/v1/embeddings?token=secret-test-value",
        provider_name="openai_compatible",
        model="shared-model-name",
        dimensions=3,
        cache_revision="weights-3",
    )

    versions = {
        provider.model_info.model_version,
        other_endpoint.model_info.model_version,
        other_revision.model_info.model_version,
    }
    assert len(versions) == 3
    assert all("secret-test-value" not in version for version in versions)


def test_openai_provider_rejects_dimension_mismatch() -> None:
    provider = OpenAIEmbeddingProvider(
        api_key="test-key",
        dimensions=3,
        client=_client(
            lambda _request: httpx.Response(
                200,
                json={"data": [{"index": 0, "embedding": [1.0, 0.0]}]},
            )
        ),
    )

    with pytest.raises(EmbeddingProviderError, match="dimension_mismatch"):
        provider.embed_batch(["texto"])


def test_openai_provider_sanitizes_http_errors() -> None:
    provider = OpenAIEmbeddingProvider(
        api_key="secret-test-value",
        dimensions=3,
        client=_client(
            lambda _request: httpx.Response(401, text="secret response body")
        ),
    )

    with pytest.raises(EmbeddingProviderError, match="provider_http_error") as exc:
        provider.embed_batch(["texto"])
    assert "secret response body" not in str(exc.value)
    assert "secret-test-value" not in str(exc.value)


def test_cosine_similarity_checks_dimensions_and_zero_vectors() -> None:
    assert cosine_similarity((1.0, 0.0), (1.0, 0.0)) == 1.0
    with pytest.raises(ValueError, match="dimensões incompatíveis"):
        cosine_similarity((1.0,), (1.0, 0.0))
    with pytest.raises(ValueError, match="norma zero"):
        cosine_similarity((0.0, 0.0), (1.0, 0.0))
