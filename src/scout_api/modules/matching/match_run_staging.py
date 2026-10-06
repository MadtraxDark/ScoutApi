"""Versioned, bounded observations for recovery; never a second catalog."""

from __future__ import annotations

import json
from typing import Any

from scout_api.modules.crawler.models.product import ProductPriceItem
from scout_api.modules.matching.schemas import MatchHit

# Commercial/identity fields only. Metadata is intentionally allowlisted.
_PRODUCT_FIELDS = set(ProductPriceItem.model_fields) - {
    "metadata",
    "images",
    "image_candidates",
}
_METADATA_FIELDS = {
    "identity_only",
    "specifications",
    "color",
    "storage",
    "ram",
    "size",
    "capacity",
    "vram",
    "edition",
    "network_lock",
    "timed_promotion",
    "category",
    "mpn",
    "socket",
    "promotion",
    "promotion_expires_at",
    "promotion_status",
    "promotion_source",
    "promotion_verified",
}
_SENSITIVE = ("token", "cookie", "password", "secret", "authorization", "proxy")
_MAX_BYTES = 65536


def _safe_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 3:
        return None
    if isinstance(value, dict):
        return {
            str(key)[:128]: _safe_value(item, depth=depth + 1)
            for key, item in list(value.items())[:80]
            if not any(word in str(key).lower() for word in _SENSITIVE)
        }
    if isinstance(value, list):
        return [_safe_value(item, depth=depth + 1) for item in value[:40]]
    if isinstance(value, str):
        return value[:2048]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:2048]


def stage_product(item: ProductPriceItem) -> dict[str, Any]:
    product = item.model_dump(mode="json", include=_PRODUCT_FIELDS)
    product["metadata"] = {
        key: _safe_value(value)
        for key, value in item.model_dump(mode="json")["metadata"].items()
        if key in _METADATA_FIELDS
    }
    payload = {"version": 1, "product": product}
    if len(json.dumps(payload).encode()) > _MAX_BYTES:
        raise ValueError("MATCH_OBSERVATION_TOO_LARGE")
    return payload


def restore_product(payload: dict[str, Any]) -> ProductPriceItem:
    if payload.get("version") != 1:
        raise ValueError("MATCH_OBSERVATION_VERSION")
    return ProductPriceItem.model_validate(payload["product"])


def stage_hit(hit: MatchHit) -> dict[str, Any]:
    payload = stage_product(hit.product)
    payload["hit"] = hit.model_dump(
        mode="json", exclude={"product", "listing_id", "search_query"}
    )
    if len(json.dumps(payload).encode()) > _MAX_BYTES:
        raise ValueError("MATCH_OBSERVATION_TOO_LARGE")
    return payload


def restore_hit(payload: dict[str, Any]) -> MatchHit:
    return MatchHit.model_validate(
        {**payload["hit"], "product": restore_product(payload)}
    )
