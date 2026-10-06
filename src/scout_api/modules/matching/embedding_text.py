"""Build comparable text representations for embedding experiments.

This module is intentionally local and side-effect free. It does not call an
embedding provider or participate in Product Match decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from scout_api.modules.matching.identity import ProductIdentity

EmbeddingRepresentation = Literal["raw", "normalized", "structured", "hybrid"]


def _single_line(value: str) -> str:
    return " ".join(value.split())


@dataclass(frozen=True)
class EmbeddingTexts:
    """Candidate input forms to compare in a labeled offline benchmark."""

    raw: str
    normalized: str
    structured: str
    hybrid: str

    def get(self, representation: EmbeddingRepresentation) -> str:
        return {
            "raw": self.raw,
            "normalized": self.normalized,
            "structured": self.structured,
            "hybrid": self.hybrid,
        }[representation]


def build_embedding_texts(identity: ProductIdentity) -> EmbeddingTexts:
    """Return deterministic representations without adding exact identifiers.

    GTIN, MPN aliases, and monitor codes remain deterministic matcher evidence;
    they are not added as structured fields (a source title may still contain
    them). Model and variant attributes remain available so the benchmark can
    measure whether the model preserves those discriminants across languages.

    Commercial condition tokens (renewed/refurbished/used/…) are stripped from
    normalized/structured forms so embedding ranking does not hide a valid
    same-product candidate solely for being Renewed.
    """
    from scout_api.modules.matching.identity import strip_condition_tokens

    raw = identity.title.strip()
    normalized = strip_condition_tokens(identity.title_normalized.strip())

    fields: list[tuple[str, str | None]] = [
        ("category", identity.category),
        ("brand", identity.brand),
        ("model", identity.model),
    ]
    fields.extend(
        (f"variant_{key}", value)
        for key, value in sorted(identity.variant_attrs.items())
    )
    structured = "\n".join(
        f"{key}: {_single_line(value)}"
        for key, value in fields
        if value and value.strip()
    )
    hybrid = "\n".join(part for part in (raw, structured) if part)

    return EmbeddingTexts(
        raw=raw,
        normalized=normalized,
        structured=structured,
        hybrid=hybrid,
    )
