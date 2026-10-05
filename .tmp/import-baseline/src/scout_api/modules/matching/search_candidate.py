"""Search candidate DTO for Product Match candidate discovery."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SearchCandidate(BaseModel):
    """Lightweight hit from a store SERP before full PDP scrape."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    url: str
    title: str | None = None
    product_id: str | None = None
    snippet_price: Decimal | None = Field(
        default=None,
        description="Preço exibido no resultado de busca, quando disponível.",
        examples=["4799.00"],
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Metadados auxiliares do resultado de busca.",
        examples=[{"source": {"snippet_price": "serp-card"}}],
    )
