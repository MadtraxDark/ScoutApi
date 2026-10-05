"""Background AVIF optimization for uploaded store logos."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from scout_api.core.config import Settings, get_settings
from scout_api.modules.images.drive_client import DriveStorage
from scout_api.modules.images.optimizer import AvifOptimizer
from scout_api.modules.matching.models import StoreMetadata

logger = logging.getLogger(__name__)


def process_store_logo_jobs(
    session: Session, *, drive: DriveStorage, settings: Settings | None = None
) -> int:
    cfg = settings or get_settings()
    stale_before = datetime.now(UTC) - timedelta(minutes=5)
    rows = list(
        session.scalars(
            select(StoreMetadata)
            .where(
                or_(
                    StoreMetadata.logo_processing_status == "pending",
                    and_(
                        StoreMetadata.logo_processing_status == "processing",
                        StoreMetadata.updated_at < stale_before,
                    ),
                )
            )
            .limit(max(1, cfg.image_optimization_batch_size))
            .with_for_update(skip_locked=True)
        ).all()
    )
    jobs = [
        (row.store_key, row.logo_version, row.logo_original_file_id) for row in rows
    ]
    for row in rows:
        row.logo_processing_status = "processing"
    session.commit()

    processed = 0
    optimizer = AvifOptimizer(cfg)
    for key, version, original_id in jobs:
        if not original_id:
            continue
        try:
            original = drive.download_bytes(original_id)
            optimized = optimizer.convert(original)
            folder = drive.ensure_folder("store-logos", parent_id=drive.root_folder_id)
            optimized_id = drive.upload_bytes(
                name=f"{key}-{version}.avif",
                parent_id=folder,
                data=optimized.data,
                mime_type="image/avif",
            )
            row = session.get(StoreMetadata, key)
            if row is None or row.logo_version != version:
                drive.delete_file(optimized_id)
                continue
            previous_optimized = row.logo_optimized_file_id
            row.logo_optimized_file_id = optimized_id
            row.logo_processing_status = "ready"
            row.logo_processing_error = None
            session.commit()
            if previous_optimized:
                drive.delete_file(previous_optimized)
            processed += 1
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            row = session.get(StoreMetadata, key)
            if row is not None and row.logo_version == version:
                row.logo_processing_status = "failed"
                row.logo_processing_error = str(exc)[:500]
                session.commit()
            logger.exception("store_logo_avif_failed store_key=%s", key)
    return processed
