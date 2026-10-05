"""Pydantic schemas for product image gallery (ADR 0029)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl

OriginalStatus = Literal["pending", "downloading", "ready", "failed", "deleting"]
OptimizedStatus = Literal["pending", "processing", "ready", "failed"]
ImageAvailability = Literal[
    "ready",
    "missing",
    "processing",
    "temporarily_unavailable",
    "permission_denied",
    "not_found",
    "storage_error",
    "invalid_reference",
]


class ApprovedImageInput(BaseModel):
    """Images approved in PriceScout review — trust boundary for clients."""

    model_config = {"extra": "forbid"}

    source_url: HttpUrl = Field(description="URL externa aprovada para persistência.")
    position: int = Field(ge=0, description="Ordem na galeria (0 = primeira).")
    is_main: bool = Field(
        default=False, description="Se true, esta é a imagem principal."
    )


class AddProductImageRequest(BaseModel):
    model_config = {"extra": "forbid"}

    source_url: HttpUrl
    position: int | None = Field(default=None, ge=0)
    is_main: bool = False


class GalleryReorderItem(BaseModel):
    model_config = {"extra": "forbid"}

    image_id: UUID
    position: int = Field(ge=0)
    is_main: bool = False


class GalleryPatchRequest(BaseModel):
    """Reorder and/or set main image (metadata only)."""

    model_config = {"extra": "forbid"}

    images: list[GalleryReorderItem] = Field(min_length=1)


class ProductImageView(BaseModel):
    image_id: UUID
    product_id: UUID
    position: int
    is_main: bool
    source_url: str
    original_url: str | None = None
    optimized_url: str | None = None
    display_url: str | None = None
    image_status: ImageAvailability
    image_error_code: str | None = None
    image_retryable: bool = False
    image_warning_code: str | None = None
    original_status: OriginalStatus
    optimized_status: OptimizedStatus
    original_width: int | None = None
    original_height: int | None = None
    optimized_error: str | None = None
    created_at: datetime
    updated_at: datetime


class ProductImageListResponse(BaseModel):
    items: list[ProductImageView] = Field(default_factory=list)
    count: int


class ProductImportStatus(BaseModel):
    product_id: UUID
    saved: bool = True
    images_registered: int
    pending: int
    processing: int
    ready: int
    error: int
