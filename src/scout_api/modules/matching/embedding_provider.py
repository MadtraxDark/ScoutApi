"""Provider contract and HTTP adapter for Product Match embeddings."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import httpx


@dataclass(frozen=True)
class EmbeddingModelInfo:
    provider: str
    model: str
    model_version: str
    dimensions: int


class EmbeddingProviderError(RuntimeError):
    """Sanitized provider failure; response bodies and credentials are omitted."""

    def __init__(self, code: str, *, retryable: bool = False) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(code)


class EmbeddingProvider(Protocol):
    @property
    def model_info(self) -> EmbeddingModelInfo: ...

    def embed_batch(self, texts: Sequence[str]) -> list[tuple[float, ...]]: ...


class OpenAIEmbeddingProvider:
    """OpenAI-compatible embeddings API implementation."""

    def __init__(
        self,
        *,
        api_key: str,
        api_url: str = "https://api.openai.com/v1/embeddings",
        provider_name: str = "openai",
        model: str = "text-embedding-3-small",
        dimensions: int = 1536,
        input_prefix: str = "",
        cache_revision: str = "v1",
        max_batch_size: int = 100,
        timeout_seconds: float = 3.0,
        client: httpx.Client | None = None,
    ) -> None:
        if provider_name == "openai" and not api_key.strip():
            raise ValueError("API key de embeddings ausente")
        if dimensions < 1:
            raise ValueError("dimensões de embedding devem ser positivas")
        self._model = model
        self._dimensions = dimensions
        self._provider_name = provider_name
        self._input_prefix = input_prefix
        self._cache_revision = cache_revision
        if max_batch_size < 1:
            raise ValueError("tamanho máximo de batch deve ser positivo")
        self._max_batch_size = max_batch_size
        self._client = client or httpx.Client(
            headers={"Authorization": f"Bearer {api_key}"} if api_key.strip() else {},
            timeout=httpx.Timeout(timeout_seconds),
        )
        self._api_url = api_url

    @property
    def model_info(self) -> EmbeddingModelInfo:
        endpoint_fingerprint = hashlib.sha256(
            self._api_url.encode("utf-8")
        ).hexdigest()[:16]
        return EmbeddingModelInfo(
            provider=self._provider_name,
            model=self._model,
            model_version=(
                f"{self._model}:{self._input_prefix}:"
                f"{self._cache_revision}:endpoint-{endpoint_fingerprint}"
            ),
            dimensions=self._dimensions,
        )

    def embed_batch(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        vectors, _ = self.embed_batch_with_usage(texts)
        return vectors

    def embed_batch_with_usage(
        self, texts: Sequence[str]
    ) -> tuple[list[tuple[float, ...]], int | None]:
        if not texts:
            return [], 0
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError("inputs de embedding devem ser textos não vazios")
        vectors: list[tuple[float, ...]] = []
        prompt_tokens = 0
        usage_known = True
        for offset in range(0, len(texts), self._max_batch_size):
            batch_vectors, batch_tokens = self._embed_one_batch(
                texts[offset : offset + self._max_batch_size]
            )
            vectors.extend(batch_vectors)
            if batch_tokens is None:
                usage_known = False
            else:
                prompt_tokens += batch_tokens
        return vectors, prompt_tokens if usage_known else None

    def _embed_one_batch(
        self, texts: Sequence[str]
    ) -> tuple[list[tuple[float, ...]], int | None]:
        try:
            response = self._client.post(
                self._api_url,
                json={
                    "model": self._model,
                    "input": [f"{self._input_prefix}{text}" for text in texts],
                    "dimensions": self._dimensions,
                    "encoding_format": "float",
                },
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException as exc:
            raise EmbeddingProviderError("timeout", retryable=True) from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            raise EmbeddingProviderError(
                "rate_limit" if status == 429 else "provider_http_error",
                retryable=status == 429 or status >= 500,
            ) from exc
        except httpx.RequestError as exc:
            raise EmbeddingProviderError("transport_error", retryable=True) from exc
        except ValueError as exc:
            raise EmbeddingProviderError("invalid_json_response") from exc

        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list) or len(data) != len(texts):
            raise EmbeddingProviderError("invalid_batch_response")
        indexed: dict[int, tuple[float, ...]] = {}
        for item in data:
            if not isinstance(item, dict):
                raise EmbeddingProviderError("invalid_embedding_item")
            index = item.get("index")
            vector = item.get("embedding")
            if (
                type(index) is not int
                or index in indexed
                or not isinstance(vector, list)
            ):
                raise EmbeddingProviderError("invalid_embedding_item")
            if len(vector) != self._dimensions:
                raise EmbeddingProviderError("dimension_mismatch")
            values: list[float] = []
            for value in vector:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise EmbeddingProviderError("invalid_embedding_value")
                number = float(value)
                if not math.isfinite(number):
                    raise EmbeddingProviderError("invalid_embedding_value")
                values.append(number)
            indexed[index] = tuple(values)
        if set(indexed) != set(range(len(texts))):
            raise EmbeddingProviderError("invalid_batch_indexes")
        usage = payload.get("usage") if isinstance(payload, dict) else None
        prompt_tokens = usage.get("prompt_tokens") if isinstance(usage, dict) else None
        if type(prompt_tokens) is not int or prompt_tokens < 0:
            prompt_tokens = None
        return [indexed[index] for index in range(len(texts))], prompt_tokens

    def close(self) -> None:
        self._client.close()


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or len(left) != len(right):
        raise ValueError("vetores vazios ou com dimensões incompatíveis")
    if any(not math.isfinite(value) for value in (*left, *right)):
        raise ValueError("vetor contém valor não finito")
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        raise ValueError("vetores de norma zero não podem ser comparados")
    return max(-1.0, min(1.0, dot / (left_norm * right_norm)))
