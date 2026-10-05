"""Shared image-gallery pipeline stats for crawl observability."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)

ImageStatus = Literal["success", "empty", "omitted", "error"]


@dataclass
class ImagePipelineStats:
    """Counts and timings for one ``extract_images`` pass."""

    store: str = ""
    source: str | None = None
    raw: int = 0
    after_filter: int = 0
    after_dedup: int = 0
    returned: int = 0
    status: ImageStatus = "empty"
    error: str | None = None
    discovery_ms: float = 0.0
    parse_ms: float = 0.0
    filter_ms: float = 0.0
    dedup_ms: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)

    def mark_returned(self, urls: list[str]) -> list[str]:
        self.returned = len(urls)
        if self.status == "error":
            return urls
        self.status = "success" if urls else "empty"
        return urls

    def to_metadata(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "image_status": self.status,
            "image_pipeline": {
                "store": self.store,
                "source": self.source,
                "images_found_raw": self.raw,
                "images_after_filter": self.after_filter,
                "images_after_dedup": self.after_dedup,
                "images_returned": self.returned,
                "image_source_discovery_ms": round(self.discovery_ms, 2),
                "image_parse_ms": round(self.parse_ms, 2),
                "image_filter_ms": round(self.filter_ms, 2),
                "image_dedup_ms": round(self.dedup_ms, 2),
                **self.extra,
            },
        }
        if self.error:
            payload["image_error"] = self.error
        return payload

    def log(self) -> None:
        logger.info(
            "image_pipeline store=%s status=%s raw=%s filter=%s dedup=%s "
            "returned=%s source=%s discovery_ms=%.1f parse_ms=%.1f "
            "filter_ms=%.1f dedup_ms=%.1f",
            self.store,
            self.status,
            self.raw,
            self.after_filter,
            self.after_dedup,
            self.returned,
            self.source,
            self.discovery_ms,
            self.parse_ms,
            self.filter_ms,
            self.dedup_ms,
        )


class Timer:
    """Simple wall-clock slice for pipeline stages."""

    def __init__(self) -> None:
        self._start = time.perf_counter()

    def ms(self) -> float:
        return (time.perf_counter() - self._start) * 1000.0
