"""Image persistence pipeline: download → original Drive → enqueue AVIF."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy.orm import Session

from scout_api.core.config import Settings, get_settings
from scout_api.core.performance import OperationCategory, timed
from scout_api.modules.crawler.core.exceptions import RequestError
from scout_api.modules.images.claim import release_claim, schedule_optimization, utcnow
from scout_api.modules.images.downloader import ImageDownloader
from scout_api.modules.images.drive_client import (
    DriveClientError,
    DriveNotConfiguredError,
    DriveStorage,
    GoogleDriveClient,
)
from scout_api.modules.images.models import ProductImage
from scout_api.modules.images.optimizer import AvifOptimizer
from scout_api.modules.images.repository import ProductImageRepository
from scout_api.modules.images.schemas import ApprovedImageInput

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class ImagePipeline:
    """Orchestrates original persistence and durable AVIF enqueue."""

    def __init__(
        self,
        session: Session,
        *,
        drive: DriveStorage | None = None,
        downloader: ImageDownloader | None = None,
        optimizer: AvifOptimizer | None = None,
        settings: Settings | None = None,
        schedule_avif: bool = True,
    ) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._drive: DriveStorage = drive or GoogleDriveClient(self._settings)
        self._downloader = downloader or ImageDownloader(self._settings)
        self._optimizer = optimizer or AvifOptimizer(self._settings)
        self._repo = ProductImageRepository(session)
        self._schedule_avif = schedule_avif

    def persist_approved(
        self,
        product_id: UUID,
        images: list[ApprovedImageInput],
    ) -> list[ProductImage]:
        if not images:
            return []
        max_n = self._settings.image_max_per_product
        if len(images) > max_n:
            raise RequestError(
                f"Máximo de {max_n} imagens por produto",
                code="INVALID_REQUEST",
            )
        main_flags = [img.is_main for img in images]
        if sum(1 for flag in main_flags if flag) > 1:
            raise RequestError(
                "No máximo uma imagem pode ser is_main=true",
                code="INVALID_REQUEST",
            )
        # Default first as main when none flagged.
        if images and not any(img.is_main for img in images):
            first = images[0]
            images = [
                ApprovedImageInput(
                    source_url=first.source_url,
                    position=first.position,
                    is_main=True,
                ),
                *images[1:],
            ]

        results: list[ProductImage] = []
        save_started = time.perf_counter()
        with timed(
            "image_pipeline_batch",
            category=OperationCategory.EXTERNAL_TOOL,
            context={"count": len(images)},
        ):
            for approved in sorted(images, key=lambda i: i.position):
                row = self._persist_one(product_id, approved)
                results.append(row)
        product_save_ms = int((time.perf_counter() - save_started) * 1000)
        logger.info(
            "product_images_persisted",
            extra={
                "product_id": str(product_id),
                "count": len(results),
                "product_save_ms": product_save_ms,
            },
        )
        return results

    def _persist_one(
        self, product_id: UUID, approved: ApprovedImageInput
    ) -> ProductImage:
        source_url = str(approved.source_url)
        existing_count = self._repo.count_for_product(product_id)
        if existing_count >= self._settings.image_max_per_product:
            raise RequestError(
                f"Máximo de {self._settings.image_max_per_product} imagens por produto",
                code="INVALID_REQUEST",
            )

        row = self._repo.create(
            product_id=product_id,
            source_url=source_url,
            position=approved.position,
            is_main=approved.is_main,
            original_status="downloading",
            optimized_status="pending",
        )
        try:
            download_started = time.perf_counter()
            downloaded = self._downloader.download(source_url)
            original_download_ms = int((time.perf_counter() - download_started) * 1000)
            dup = self._repo.find_by_sha256(product_id, downloaded.sha256)
            if dup is not None and dup.id != row.id:
                # Drop the placeholder and reuse existing.
                self._repo.delete(row)
                if (
                    self._schedule_avif
                    and dup.original_status == "ready"
                    and dup.optimized_status not in {"ready", "processing"}
                ):
                    self.enqueue_optimization(dup.id)
                return dup

            products_folder = self._drive.ensure_folder(
                "products", parent_id=self._root_folder_id()
            )
            product_folder = self._drive.ensure_folder(
                str(product_id), parent_id=products_folder
            )
            original_folder = self._drive.ensure_folder(
                "original", parent_id=product_folder
            )
            filename = f"{row.id}.{downloaded.extension}"
            upload_started = time.perf_counter()
            with timed(
                "image_original_upload",
                category=OperationCategory.EXTERNAL_TOOL,
            ):
                file_id = self._drive.upload_bytes(
                    name=filename,
                    parent_id=original_folder,
                    data=downloaded.data,
                    mime_type=downloaded.content_type,
                )
            original_upload_ms = int((time.perf_counter() - upload_started) * 1000)
            row.original_drive_file_id = file_id
            row.original_filename = filename
            row.original_mime_type = downloaded.content_type
            row.original_size_bytes = len(downloaded.data)
            row.original_width = downloaded.width
            row.original_height = downloaded.height
            row.original_sha256 = downloaded.sha256
            row.original_status = "ready"
            self._session.flush()

            enqueue_started = time.perf_counter()
            if self._schedule_avif:
                self.enqueue_optimization(row.id)
            optimization_enqueue_ms = int(
                (time.perf_counter() - enqueue_started) * 1000
            )
            logger.info(
                "image_original_ready",
                extra={
                    "image_id": str(row.id),
                    "product_id": str(product_id),
                    "original_download_ms": original_download_ms,
                    "original_upload_ms": original_upload_ms,
                    "optimization_enqueue_ms": optimization_enqueue_ms,
                    "optimized_status": row.optimized_status,
                },
            )
            return row
        except (RequestError, DriveNotConfiguredError, DriveClientError) as exc:
            row.original_status = "failed"
            row.optimized_status = "failed"
            row.optimized_error = str(exc)[:500]
            release_claim(row)
            self._session.flush()
            if isinstance(exc, RequestError):
                raise
            raise RequestError(str(exc), code="STORAGE_ERROR") from exc

    def _root_folder_id(self) -> str:
        if isinstance(self._drive, GoogleDriveClient):
            return self._drive.root_folder_id
        root = getattr(self._drive, "root_folder_id", None)
        if callable(root):
            return str(root())
        if isinstance(root, str) and root:
            return root
        ensure = getattr(self._drive, "ensure_folder", None)
        if ensure is not None and hasattr(self._drive, "folders"):
            return "root"
        raise DriveNotConfiguredError("Drive root folder ausente")

    def enqueue_optimization(self, image_id: UUID) -> None:
        """Persist durable pending state. Conversion runs in the worker.

        Does **not** convert in the request path. Safe before commit: the
        worker recovers ``pending`` rows after restart / next sweep.
        """
        row = self._repo.get(image_id)
        if row is None:
            return
        if row.original_status != "ready" or not row.original_drive_file_id:
            return
        if row.optimized_status == "ready" and row.optimized_drive_file_id:
            return
        with timed(
            "optimization_enqueue",
            category=OperationCategory.DATABASE_QUERY,
            context={"image_id": str(image_id)},
        ):
            schedule_optimization(row, when=utcnow())
            self._session.flush()
        # Best-effort wake; poller also recovers after commit.
        try:
            from scout_api.modules.images.worker import notify_optimizer

            notify_optimizer()
        except Exception:  # noqa: BLE001
            logger.debug("optimizer notify skipped", exc_info=True)

    def optimize_now(
        self,
        image: ProductImage,
        *,
        already_claimed: bool = False,
    ) -> ProductImage:
        """Synchronous AVIF (worker / tests / explicit sync retry)."""
        if image.original_status != "ready" or not image.original_drive_file_id:
            raise RequestError(
                "Original não está ready para otimização",
                code="INVALID_REQUEST",
            )
        if image.optimized_status == "ready" and image.optimized_drive_file_id:
            release_claim(image)
            self._session.flush()
            return image

        if not already_claimed:
            image.optimized_status = "processing"
            image.optimized_error = None
            self._session.flush()

        conversion_ms = 0
        upload_ms = 0
        try:
            original_bytes = self._drive.download_bytes(image.original_drive_file_id)
            conv_started = time.perf_counter()
            optimized = self._optimizer.convert(original_bytes)
            conversion_ms = int((time.perf_counter() - conv_started) * 1000)
            products_folder = self._drive.ensure_folder(
                "products", parent_id=self._root_folder_id()
            )
            product_folder = self._drive.ensure_folder(
                str(image.canonical_product_id), parent_id=products_folder
            )
            opt_folder = self._drive.ensure_folder(
                "optimized", parent_id=product_folder
            )
            filename = f"{image.id}.avif"
            upload_started = time.perf_counter()
            with timed(
                "avif_upload",
                category=OperationCategory.EXTERNAL_TOOL,
            ):
                file_id = self._drive.upload_bytes(
                    name=filename,
                    parent_id=opt_folder,
                    data=optimized.data,
                    mime_type=optimized.mime_type,
                )
            upload_ms = int((time.perf_counter() - upload_started) * 1000)
            image.optimized_drive_file_id = file_id
            image.optimized_mime_type = optimized.mime_type
            image.optimized_size_bytes = optimized.size_bytes
            image.optimized_width = optimized.width
            image.optimized_height = optimized.height
            image.optimized_status = "ready"
            image.optimized_error = None
            release_claim(image)
            self._session.flush()
            logger.info(
                "avif_ready",
                extra={
                    "image_id": str(image.id),
                    "product_id": str(image.canonical_product_id),
                    "avif_conversion_ms": conversion_ms,
                    "avif_upload_ms": upload_ms,
                    "original_size": image.original_size_bytes,
                    "optimized_size": image.optimized_size_bytes,
                },
            )
            return image
        except Exception as exc:
            logger.exception("AVIF failed for image %s", image.id)
            image.optimized_status = "failed"
            image.optimized_error = str(exc)[:500]
            release_claim(image)
            self._session.flush()
            return image

    def delete_image_files(self, image: ProductImage) -> None:
        for file_id in (
            image.optimized_drive_file_id,
            image.original_drive_file_id,
        ):
            if not file_id:
                continue
            try:
                self._drive.delete_file(file_id)
            except DriveClientError as exc:
                if exc.availability == "not_found":
                    continue
                raise
