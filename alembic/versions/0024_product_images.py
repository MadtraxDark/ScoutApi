"""Create product_images table for Drive-backed gallery (ADR 0029)."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_product_images"
down_revision: str | None = "0023_canonical_owner_user_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "product_images",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "canonical_product_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("canonical_products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("original_drive_file_id", sa.String(length=128), nullable=True),
        sa.Column("original_filename", sa.String(length=256), nullable=True),
        sa.Column("original_mime_type", sa.String(length=128), nullable=True),
        sa.Column("original_size_bytes", sa.Integer(), nullable=True),
        sa.Column("original_width", sa.Integer(), nullable=True),
        sa.Column("original_height", sa.Integer(), nullable=True),
        sa.Column("original_sha256", sa.String(length=64), nullable=True),
        sa.Column(
            "original_status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("optimized_drive_file_id", sa.String(length=128), nullable=True),
        sa.Column("optimized_mime_type", sa.String(length=128), nullable=True),
        sa.Column("optimized_size_bytes", sa.Integer(), nullable=True),
        sa.Column("optimized_width", sa.Integer(), nullable=True),
        sa.Column("optimized_height", sa.Integer(), nullable=True),
        sa.Column(
            "optimized_status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("optimized_error", sa.Text(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_main", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint(
            "canonical_product_id",
            "id",
            name="uq_product_images_product_id_pair",
        ),
    )
    op.create_index(
        "ix_product_images_canonical_product_id",
        "product_images",
        ["canonical_product_id"],
    )
    op.create_index(
        "ix_product_images_product_position",
        "product_images",
        ["canonical_product_id", "position"],
    )
    op.create_index(
        "uq_product_images_product_sha256",
        "product_images",
        ["canonical_product_id", "original_sha256"],
        unique=True,
        postgresql_where=sa.text("original_sha256 IS NOT NULL"),
        sqlite_where=sa.text("original_sha256 IS NOT NULL"),
    )
    op.create_index(
        "uq_product_images_product_main",
        "product_images",
        ["canonical_product_id"],
        unique=True,
        postgresql_where=sa.text("is_main IS TRUE"),
        sqlite_where=sa.text("is_main = 1"),
    )


def downgrade() -> None:
    op.drop_index("uq_product_images_product_main", table_name="product_images")
    op.drop_index("uq_product_images_product_sha256", table_name="product_images")
    op.drop_index("ix_product_images_product_position", table_name="product_images")
    op.drop_index("ix_product_images_canonical_product_id", table_name="product_images")
    op.drop_table("product_images")
