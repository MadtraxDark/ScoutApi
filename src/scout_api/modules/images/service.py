"""Product image gallery service (CRUD, delivery, cleanup)."""

from __future__ import annotations

import logging
from typing import Literal
from uuid import UUID

from sqlalchemy.orm import Session

from scout_api.core.config import Settings, get_settings
from scout_api.modules.auth.schemas import AuthenticatedPrincipal
from scout_api.modules.crawler.core.exceptions import RequestError
from scout_api.modules.images.drive_client import (
    DriveClientError,
    DriveStorage,
    get_drive_storage,
)
from scout_api.modules.images.models import ProductImage
from scout_api.modules.images.pipeline import ImagePipeline
from scout_api.modules.images.repository import ProductImageRepository
from scout_api.modules.images.schemas import (
    AddProductImageRequest,
    ApprovedImageInput,
    GalleryPatchRequest,
    ImageAvailability,
    ProductImageListResponse,
    ProductImageView,
    ProductImportStatus,
)
from scout_api.modules.matching.repository import MatchingRepository

logger = logging.getLogger(__name__)

ContentVariant = Literal["auto", "original", "optimized"]


def content_path(
    product_id: UUID,
    image_id: UUID,
    *,
    variant: ContentVariant | None = None,
) -> str:
    base = f"/products/{product_id}/images/{image_id}/content"
    if variant in {"original", "optimized"}:
        return f"{base}?variant={variant}"
    return base


def to_image_view(row: ProductImage) -> ProductImageView:
    display_url: str | None
    image_status: ImageAvailability
    product_id = row.canonical_product_id
    original_url = (
        content_path(product_id, row.id, variant="original")
        if row.original_status == "ready" and row.original_drive_file_id
        else row.source_url
        if row.original_status in {"pending", "downloading"}
        else None
    )
    optimized_url = (
        content_path(product_id, row.id, variant="optimized")
        if row.optimized_status == "ready" and row.optimized_drive_file_id
        else None
    )
    if row.optimized_status == "ready" and optimized_url:
        display_url = optimized_url
    elif row.original_status == "ready":
        display_url = original_url
    else:
        display_url = original_url
    if row.optimized_status == "ready" and row.optimized_drive_file_id:
        image_status = "ready"
        image_error_code = None
    elif row.original_status == "ready":
        if row.original_drive_file_id:
            image_status = "ready"
            image_error_code = None
        else:
            image_status = "invalid_reference"
            image_error_code = "invalid_reference"
    elif row.original_status in {"pending", "downloading"}:
        image_status = "processing"
        image_error_code = None
    elif row.original_status == "failed":
        image_status = "storage_error"
        image_error_code = "storage_error"
    else:
        image_status = "processing"
        image_error_code = None
    optimized_failed = row.optimized_status == "failed" or (
        row.optimized_status == "ready" and not row.optimized_drive_file_id
    )
    return ProductImageView(
        image_id=row.id,
        product_id=product_id,
        position=row.position,
        is_main=row.is_main,
        source_url=row.source_url,
        original_url=original_url,
        optimized_url=optimized_url,
        display_url=display_url,
        image_status=image_status,
        image_error_code=image_error_code,
        image_retryable=image_status == "storage_error",
        image_warning_code=(
            "conversion_failed"
            if image_status == "ready" and optimized_failed
            else None
        ),
        original_status=row.original_status,  # type: ignore[arg-type]
        optimized_status=row.optimized_status,  # type: ignore[arg-type]
        original_width=row.original_width,
        original_height=row.original_height,
        optimized_error=(
            "A versão otimizada não pôde ser gerada; "
            "a imagem original segue disponível."
            if row.original_status == "ready" and optimized_failed
            else None
        ),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def primary_display_url(images: list[ProductImageView]) -> str | None:
    """Canonical card thumbnail: AVIF if ready, else original."""
    if not images:
        return None
    main = next((img for img in images if img.is_main), None)
    chosen = main or images[0]
    return chosen.display_url


def _image_storage_error(
    exc: DriveClientError, *, product_id: UUID, image_id: UUID
) -> RequestError:
    public_code = {
        "not_found": "IMAGE_STORAGE_NOT_FOUND",
        "permission_denied": "IMAGE_STORAGE_PERMISSION_DENIED",
        "temporarily_unavailable": "IMAGE_STORAGE_UNAVAILABLE",
    }.get(exc.availability, "IMAGE_STORAGE_ERROR")
    logger.error(
        "image_delivery_failed image_id=%s entity_type=product entity_id=%s "
        "storage_provider=drive error_code=%s http_status=%s retryable=%s",
        image_id,
        product_id,
        exc.code,
        exc.http_status,
        exc.retryable,
    )
    message = {
        "IMAGE_STORAGE_NOT_FOUND": (
            "A imagem cadastrada não foi encontrada no armazenamento."
        ),
        "IMAGE_STORAGE_PERMISSION_DENIED": (
            "A imagem existe, mas o sistema não conseguiu acessá-la."
        ),
        "IMAGE_STORAGE_UNAVAILABLE": (
            "Não foi possível carregar a imagem no momento. Tente novamente mais tarde."
        ),
    }.get(public_code, "Não foi possível acessar a imagem cadastrada.")
    return RequestError(
        message,
        code=public_code,
        retryable=exc.retryable,
        upstream_status=exc.http_status,
    )


class ProductImageService:
    def __init__(
        self,
        session: Session,
        *,
        drive: DriveStorage | None = None,
        settings: Settings | None = None,
        schedule_avif: bool = True,
    ) -> None:
        self._session = session
        self._settings = settings or get_settings()
        if drive is not None:
            self._drive = drive
        else:
            # Dev/test without Drive credentials uses process-local storage.
            self._drive = get_drive_storage(self._settings)
        self._repo = ProductImageRepository(session)
        self._pipeline = ImagePipeline(
            session,
            drive=self._drive,
            settings=self._settings,
            schedule_avif=schedule_avif,
        )

    def _require_product(
        self, product_id: UUID, principal: AuthenticatedPrincipal
    ) -> None:
        from scout_api.modules.matching.product_registration_service import (
            can_access_product,
        )

        matching = MatchingRepository(self._session)
        product = matching.get_canonical(product_id)
        if product is None or not can_access_product(product, principal):
            raise RequestError(
                "Produto canônico não encontrado",
                code="PRODUCT_NOT_FOUND",
            )

    def list_images(
        self, product_id: UUID, *, viewer: AuthenticatedPrincipal
    ) -> ProductImageListResponse:
        self._require_product(product_id, viewer)
        rows = self._repo.list_for_product(product_id)
        items = [to_image_view(row) for row in rows]
        return ProductImageListResponse(items=items, count=len(items))

    def persist_approved(
        self,
        product_id: UUID,
        images: list[ApprovedImageInput],
        *,
        owner: AuthenticatedPrincipal,
    ) -> list[ProductImageView]:
        self._require_product(product_id, owner)
        rows = self._pipeline.persist_approved(product_id, images)
        return [to_image_view(row) for row in rows]

    def register_references(
        self,
        product_id: UUID,
        images: list[ApprovedImageInput],
        *,
        owner: AuthenticatedPrincipal,
    ) -> list[ProductImageView]:
        self._require_product(product_id, owner)
        MatchingRepository(self._session).lock_canonical(product_id)
        rows = self._pipeline.register_references(product_id, images)
        return [to_image_view(row) for row in rows]

    def import_status(
        self, product_id: UUID, *, viewer: AuthenticatedPrincipal
    ) -> ProductImportStatus:
        self._require_product(product_id, viewer)
        rows = self._repo.list_for_product(product_id)
        counts = dict(pending=0, processing=0, ready=0, error=0)
        for row in rows:
            if row.original_status == "failed" or row.optimized_status == "failed":
                counts["error"] += 1
            elif row.optimized_status == "ready":
                counts["ready"] += 1
            elif (
                row.original_status == "downloading"
                or row.optimized_status == "processing"
            ):
                counts["processing"] += 1
            else:
                counts["pending"] += 1
        return ProductImportStatus(
            product_id=product_id,
            images_registered=len(rows),
            pending=counts["pending"],
            processing=counts["processing"],
            ready=counts["ready"],
            error=counts["error"],
        )

    def add_image(
        self,
        product_id: UUID,
        request: AddProductImageRequest,
        *,
        owner: AuthenticatedPrincipal,
    ) -> ProductImageView:
        self._require_product(product_id, owner)
        position = (
            request.position
            if request.position is not None
            else self._repo.next_position(product_id)
        )
        approved = ApprovedImageInput(
            source_url=request.source_url,
            position=position,
            is_main=request.is_main,
        )
        rows = self._pipeline.persist_approved(product_id, [approved])
        return to_image_view(rows[0])

    def patch_gallery(
        self,
        product_id: UUID,
        request: GalleryPatchRequest,
        *,
        owner: AuthenticatedPrincipal,
    ) -> ProductImageListResponse:
        self._require_product(product_id, owner)
        try:
            rows = self._repo.apply_positions(
                product_id,
                [
                    (item.image_id, item.position, item.is_main)
                    for item in request.images
                ],
            )
        except ValueError as exc:
            raise RequestError(str(exc), code="INVALID_REQUEST") from exc
        items = [to_image_view(row) for row in rows]
        return ProductImageListResponse(items=items, count=len(items))

    def delete_image(
        self,
        product_id: UUID,
        image_id: UUID,
        *,
        owner: AuthenticatedPrincipal,
    ) -> None:
        self._require_product(product_id, owner)
        row = self._repo.get(image_id, product_id=product_id)
        if row is None:
            return
        row.original_status = "deleting"
        self._session.flush()
        try:
            self._pipeline.delete_image_files(row)
        except DriveClientError as exc:
            logger.error("Drive cleanup failed for image %s: %s", image_id, exc)
            raise RequestError(
                "Falha parcial ao excluir arquivos no Drive; tente novamente",
                code="STORAGE_ERROR",
                retryable=True,
            ) from exc
        was_main = row.is_main
        self._repo.delete(row)
        self._repo.resequence(product_id)
        if was_main:
            remaining = self._repo.list_for_product(product_id)
            if remaining and not any(r.is_main for r in remaining):
                self._repo.set_main(remaining[0])

    def retry_optimization(
        self,
        product_id: UUID,
        image_id: UUID,
        *,
        owner: AuthenticatedPrincipal,
        sync: bool = False,
    ) -> ProductImageView:
        self._require_product(product_id, owner)
        row = self._repo.get(image_id, product_id=product_id)
        if row is None:
            raise RequestError(
                "Imagem não encontrada",
                code="PRODUCT_NOT_FOUND",
            )
        if row.original_status != "ready":
            from scout_api.modules.images.claim import schedule_optimization

            if row.original_status == "failed":
                row.original_status = "pending"
                schedule_optimization(row)
                self._session.flush()
            return to_image_view(row)
        # Reuse persisted original — never re-download.
        self._pipeline.enqueue_optimization(row.id)
        if sync:
            refreshed = self._repo.get(image_id, product_id=product_id)
            assert refreshed is not None
            self._pipeline.optimize_now(refreshed)
        refreshed = self._repo.get(image_id, product_id=product_id)
        assert refreshed is not None
        return to_image_view(refreshed)

    def get_content(
        self,
        product_id: UUID,
        image_id: UUID,
        *,
        viewer: AuthenticatedPrincipal,
        variant: ContentVariant = "auto",
    ) -> tuple[bytes, str, str]:
        """Return (bytes, content_type, etag)."""
        self._require_product(product_id, viewer)
        row = self._repo.get(image_id, product_id=product_id)
        if row is None:
            raise RequestError("Imagem não encontrada", code="PRODUCT_NOT_FOUND")

        prefer_optimized = variant == "optimized" or (
            variant == "auto" and row.optimized_status == "ready"
        )
        if prefer_optimized and row.optimized_drive_file_id:
            if row.optimized_status != "ready" and variant == "optimized":
                raise RequestError(
                    "Versão otimizada ainda não disponível",
                    code="INVALID_REQUEST",
                )
            if row.optimized_status == "ready":
                try:
                    data = self._drive.download_bytes(row.optimized_drive_file_id)
                except DriveClientError as exc:
                    raise _image_storage_error(
                        exc, product_id=product_id, image_id=image_id
                    ) from exc
                ctype = row.optimized_mime_type or "image/avif"
                etag = row.original_sha256 or str(row.id)
                logger.info(
                    "media_content product_id=%s image_id=%s original_status=%s "
                    "optimized_status=%s selected_source=optimized "
                    "storage_provider=drive media_response_status=200 content_type=%s",
                    product_id,
                    image_id,
                    row.original_status,
                    row.optimized_status,
                    ctype,
                )
                return data, ctype, f'"{etag}-avif"'

        if variant == "optimized":
            raise RequestError(
                "Versão otimizada ainda não disponível",
                code="INVALID_REQUEST",
            )

        if row.original_status == "ready" and row.original_drive_file_id:
            try:
                data = self._drive.download_bytes(row.original_drive_file_id)
            except DriveClientError as exc:
                raise _image_storage_error(
                    exc, product_id=product_id, image_id=image_id
                ) from exc
            ctype = row.original_mime_type or "application/octet-stream"
            etag = row.original_sha256 or str(row.id)
            logger.info(
                "media_content product_id=%s image_id=%s original_status=%s "
                "optimized_status=%s selected_source=original "
                "storage_provider=drive media_response_status=200 content_type=%s",
                product_id,
                image_id,
                row.original_status,
                row.optimized_status,
                ctype,
            )
            return data, ctype, f'"{etag}-original"'
        logger.warning(
            "media_content product_id=%s image_id=%s original_status=%s "
            "optimized_status=%s selected_source=none media_response_status=422",
            product_id,
            image_id,
            row.original_status,
            row.optimized_status,
        )
        if row.original_status == "ready" and not row.original_drive_file_id:
            raise RequestError(
                "A referência da imagem está inválida.",
                code="IMAGE_INVALID_REFERENCE",
            )
        raise RequestError(
            "A imagem ainda está sendo processada.",
            code="IMAGE_PROCESSING",
            retryable=True,
        )

    def cleanup_product_images(self, product_id: UUID) -> None:
        """Best-effort Drive cleanup before product cascade delete."""
        rows = self._repo.list_for_product(product_id)
        for row in rows:
            try:
                self._pipeline.delete_image_files(row)
            except DriveClientError:
                logger.exception(
                    "Failed Drive cleanup for image %s on product delete",
                    row.id,
                )
