"""SQLAlchemy ORM for catalog product images (ADR 0029)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from scout_api.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ProductImage(Base):
    __tablename__ = "product_images"
    __table_args__ = (
        Index("ix_product_images_product_position", "canonical_product_id", "position"),
        Index(
            "ix_product_images_optimization_due",
            "optimized_status",
            "optimization_next_attempt_at",
        ),
        Index(
            "uq_product_images_product_sha256",
            "canonical_product_id",
            "original_sha256",
            unique=True,
            postgresql_where=text("original_sha256 IS NOT NULL"),
            sqlite_where=text("original_sha256 IS NOT NULL"),
        ),
        Index(
            "uq_product_images_product_main",
            "canonical_product_id",
            unique=True,
            postgresql_where=text("is_main IS TRUE"),
            sqlite_where=text("is_main = 1"),
        ),
        UniqueConstraint(
            "canonical_product_id",
            "id",
            name="uq_product_images_product_id_pair",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    canonical_product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("canonical_products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_url: Mapped[str] = mapped_column(Text, nullable=False)

    original_drive_file_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    original_filename: Mapped[str | None] = mapped_column(String(256), nullable=True)
    original_mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    original_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    original_width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    original_height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    original_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    original_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending"
    )

    optimized_drive_file_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    optimized_mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    optimized_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    optimized_width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    optimized_height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    optimized_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending"
    )
    optimized_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Durable AVIF job lease (ADR 0031) — survives API restart.
    optimization_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    optimization_next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    optimization_claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    optimization_claim_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    optimization_worker_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )

    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_main: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    # Relationship declared without back_populates on CanonicalProduct to avoid
    # circular import; cascade is owned by FK ondelete=CASCADE.
    canonical_product = relationship(
        "CanonicalProduct",
        foreign_keys=[canonical_product_id],
        lazy="select",
    )


# Register the FK target mapper for standalone workers (image-optimizer) that
# import ProductImage without going through the FastAPI matching stack.
from scout_api.modules.matching import models as _matching_models  # noqa: E402, F401
