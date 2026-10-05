"""Repository for product_images rows."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from scout_api.modules.images.models import ProductImage


class ProductImageRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def lock_processing(self, product_id: uuid.UUID) -> None:
        """Coordinate workers without locking the product row used by imports."""
        if self._session.get_bind().dialect.name == "postgresql":
            self._session.execute(
                select(
                    func.pg_advisory_xact_lock(
                        func.hashtextextended(f"product-images:{product_id}", 0)
                    )
                )
            )

    def list_for_product(self, product_id: uuid.UUID) -> list[ProductImage]:
        stmt = (
            select(ProductImage)
            .where(ProductImage.canonical_product_id == product_id)
            .where(ProductImage.original_status != "deleting")
            .order_by(ProductImage.position.asc(), ProductImage.created_at.asc())
        )
        return list(self._session.scalars(stmt).all())

    def list_for_products(
        self, product_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[ProductImage]]:
        """Batch gallery load — one query for many products (avoids N+1)."""
        if not product_ids:
            return {}
        stmt = (
            select(ProductImage)
            .where(ProductImage.canonical_product_id.in_(product_ids))
            .where(ProductImage.original_status != "deleting")
            .order_by(
                ProductImage.canonical_product_id.asc(),
                ProductImage.position.asc(),
                ProductImage.created_at.asc(),
            )
        )
        grouped: dict[uuid.UUID, list[ProductImage]] = {
            product_id: [] for product_id in product_ids
        }
        for row in self._session.scalars(stmt).all():
            grouped.setdefault(row.canonical_product_id, []).append(row)
        return grouped

    def get(
        self, image_id: uuid.UUID, *, product_id: uuid.UUID | None = None
    ) -> ProductImage | None:
        stmt = select(ProductImage).where(ProductImage.id == image_id)
        if product_id is not None:
            stmt = stmt.where(ProductImage.canonical_product_id == product_id)
        return self._session.scalars(stmt).first()

    def find_by_sha256(self, product_id: uuid.UUID, sha256: str) -> ProductImage | None:
        stmt = (
            select(ProductImage)
            .where(ProductImage.canonical_product_id == product_id)
            .where(ProductImage.original_sha256 == sha256)
            .where(ProductImage.original_status != "deleting")
        )
        return self._session.scalars(stmt).first()

    def count_for_product(self, product_id: uuid.UUID) -> int:
        return len(self.list_for_product(product_id))

    def find_ready_optimization(self, image: ProductImage) -> ProductImage | None:
        return self._session.scalars(
            select(ProductImage).where(
                ProductImage.canonical_product_id == image.canonical_product_id,
                ProductImage.id != image.id,
                ProductImage.original_drive_file_id == image.original_drive_file_id,
                ProductImage.optimized_status == "ready",
                ProductImage.optimized_drive_file_id.is_not(None),
            )
        ).first()

    def create(
        self,
        *,
        product_id: uuid.UUID,
        source_url: str,
        position: int,
        is_main: bool,
        original_status: str = "pending",
        optimized_status: str = "pending",
        **extra: Any,
    ) -> ProductImage:
        if is_main:
            self.clear_main(product_id)
        row = ProductImage(
            canonical_product_id=product_id,
            source_url=source_url,
            position=position,
            is_main=is_main,
            original_status=original_status,
            optimized_status=optimized_status,
            **extra,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def clear_main(self, product_id: uuid.UUID) -> None:
        self._session.execute(
            update(ProductImage)
            .where(ProductImage.canonical_product_id == product_id)
            .where(ProductImage.is_main.is_(True))
            .values(is_main=False)
        )

    def set_main(self, image: ProductImage) -> None:
        self.clear_main(image.canonical_product_id)
        image.is_main = True
        self._session.flush()

    def apply_positions(
        self, product_id: uuid.UUID, items: list[tuple[uuid.UUID, int, bool]]
    ) -> list[ProductImage]:
        rows = {row.id: row for row in self.list_for_product(product_id)}
        if set(rows) != {item[0] for item in items}:
            raise ValueError("PATCH deve incluir todas as imagens do produto")
        main_count = sum(1 for _, _, is_main in items if is_main)
        if main_count != 1:
            raise ValueError("Exactamente uma imagem deve ser is_main=true")
        self.clear_main(product_id)
        for image_id, position, is_main in items:
            row = rows[image_id]
            row.position = position
            row.is_main = is_main
        self._session.flush()
        return self.list_for_product(product_id)

    def delete(self, image: ProductImage) -> None:
        self._session.delete(image)
        self._session.flush()

    def next_position(self, product_id: uuid.UUID) -> int:
        rows = self.list_for_product(product_id)
        if not rows:
            return 0
        return max(r.position for r in rows) + 1

    def resequence(self, product_id: uuid.UUID) -> None:
        rows = self.list_for_product(product_id)
        for idx, row in enumerate(rows):
            row.position = idx
        if rows and not any(r.is_main for r in rows):
            rows[0].is_main = True
        self._session.flush()
