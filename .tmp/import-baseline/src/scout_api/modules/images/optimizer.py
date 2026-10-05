"""AVIF optimization via Pillow (no upscale)."""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass

from PIL import Image

from scout_api.core.config import Settings, get_settings
from scout_api.core.performance import OperationCategory, timed

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class OptimizedImage:
    data: bytes
    mime_type: str
    width: int
    height: int
    size_bytes: int


class AvifOptimizer:
    """Convert approved originals to AVIF. Never upscales."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def convert(
        self, original: bytes, *, max_dimension: int | None = None
    ) -> OptimizedImage:
        quality = self._settings.image_avif_quality
        limit = max_dimension or self._settings.image_max_dimension
        with timed(
            "image_avif_conversion",
            category=OperationCategory.EXTERNAL_TOOL,
            context={"quality": quality},
        ):
            with Image.open(io.BytesIO(original)) as img:
                img.load()
                if img.mode not in {"RGB", "RGBA"}:
                    img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
                width, height = img.size
                longest = max(width, height)
                if longest > limit:
                    scale = limit / float(longest)
                    new_size = (
                        max(1, int(width * scale)),
                        max(1, int(height * scale)),
                    )
                    img = img.resize(new_size, Image.Resampling.LANCZOS)
                    width, height = img.size
                out = io.BytesIO()
                img.save(out, format="AVIF", quality=quality)
                data = out.getvalue()
                return OptimizedImage(
                    data=data,
                    mime_type="image/avif",
                    width=width,
                    height=height,
                    size_bytes=len(data),
                )
