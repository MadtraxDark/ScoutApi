"""Claim due AVIF optimization jobs with lease + SKIP LOCKED (PostgreSQL)."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import Select, and_, or_, select, update
from sqlalchemy.orm import Session

from scout_api.core.config import Settings, get_settings
from scout_api.modules.images.models import ProductImage

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(UTC)


def _due_filter(*, now: datetime) -> object:
    lease_expired = and_(
        ProductImage.optimized_status == "processing",
        or_(
            ProductImage.optimization_claim_expires_at.is_(None),
            ProductImage.optimization_claim_expires_at <= now,
        ),
    )
    pending_due = and_(
        ProductImage.optimized_status == "pending",
        or_(
            ProductImage.optimization_next_attempt_at.is_(None),
            ProductImage.optimization_next_attempt_at <= now,
        ),
        or_(
            ProductImage.optimization_claim_expires_at.is_(None),
            ProductImage.optimization_claim_expires_at <= now,
        ),
    )
    return and_(
        ProductImage.original_status == "ready",
        ProductImage.original_drive_file_id.is_not(None),
        or_(pending_due, lease_expired),
    )


def claim_due_optimizations(
    session: Session,
    *,
    worker_id: str,
    limit: int | None = None,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> list[ProductImage]:
    """Atomically claim up to ``limit`` images needing AVIF conversion."""
    cfg = settings or get_settings()
    moment = now or utcnow()
    batch = max(1, int(limit or cfg.image_optimization_batch_size))
    lease = timedelta(seconds=max(30, int(cfg.image_optimization_lease_seconds)))
    claim_expires = moment + lease

    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        return _claim_postgres(
            session,
            worker_id=worker_id,
            batch=batch,
            now=moment,
            claim_expires=claim_expires,
        )
    return _claim_portable(
        session,
        worker_id=worker_id,
        batch=batch,
        now=moment,
        claim_expires=claim_expires,
    )


def _claim_postgres(
    session: Session,
    *,
    worker_id: str,
    batch: int,
    now: datetime,
    claim_expires: datetime,
) -> list[ProductImage]:
    stmt: Select[tuple[ProductImage]] = (
        select(ProductImage)
        .where(_due_filter(now=now))
        .order_by(
            ProductImage.optimization_next_attempt_at.asc().nulls_first(),
            ProductImage.created_at.asc(),
        )
        .limit(batch)
        .with_for_update(skip_locked=True)
    )
    rows = list(session.scalars(stmt).all())
    claimed: list[ProductImage] = []
    for image in rows:
        if image.optimized_status == "ready" and image.optimized_drive_file_id:
            continue
        image.optimized_status = "processing"
        image.optimized_error = None
        image.optimization_worker_id = worker_id
        image.optimization_claimed_at = now
        image.optimization_claim_expires_at = claim_expires
        image.optimization_attempts = int(image.optimization_attempts or 0) + 1
        claimed.append(image)
    session.flush()
    return claimed


def _claim_portable(
    session: Session,
    *,
    worker_id: str,
    batch: int,
    now: datetime,
    claim_expires: datetime,
) -> list[ProductImage]:
    """Optimistic claim for SQLite tests (no SKIP LOCKED)."""
    candidates = list(
        session.scalars(
            select(ProductImage)
            .where(_due_filter(now=now))
            .order_by(
                ProductImage.optimization_next_attempt_at.asc(),
                ProductImage.created_at.asc(),
            )
            .limit(batch * 3)
        ).all()
    )
    claimed: list[ProductImage] = []
    for image in candidates:
        if len(claimed) >= batch:
            break
        if image.optimized_status == "ready" and image.optimized_drive_file_id:
            continue
        result = session.execute(
            update(ProductImage)
            .where(
                ProductImage.id == image.id,
                ProductImage.original_status == "ready",
                or_(
                    ProductImage.optimized_status == "pending",
                    ProductImage.optimized_status == "processing",
                ),
                or_(
                    ProductImage.optimization_claim_expires_at.is_(None),
                    ProductImage.optimization_claim_expires_at <= now,
                ),
            )
            .values(
                optimized_status="processing",
                optimized_error=None,
                optimization_worker_id=worker_id,
                optimization_claimed_at=now,
                optimization_claim_expires_at=claim_expires,
                optimization_attempts=int(image.optimization_attempts or 0) + 1,
            )
        )
        if int(result.rowcount or 0) == 1:  # type: ignore[attr-defined]
            session.refresh(image)
            claimed.append(image)
    session.flush()
    return claimed


def release_claim(image: ProductImage) -> None:
    image.optimization_worker_id = None
    image.optimization_claimed_at = None
    image.optimization_claim_expires_at = None


def schedule_optimization(image: ProductImage, *, when: datetime | None = None) -> None:
    """Mark image as due for background AVIF (idempotent if already ready)."""
    if image.optimized_status == "ready" and image.optimized_drive_file_id:
        return
    image.optimized_status = "pending"
    image.optimized_error = None
    image.optimization_next_attempt_at = when or utcnow()
    release_claim(image)


def new_worker_id() -> str:
    return f"img-{uuid.uuid4().hex[:12]}"
