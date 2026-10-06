"""Add owner_user_id to canonical_products for BOLA checks."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_canonical_owner_user_id"
down_revision: str | None = "0022_store_listing_dedup"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "canonical_products",
        sa.Column("owner_user_id", sa.Uuid(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_canonical_products_owner_user_id",
        "canonical_products",
        ["owner_user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_canonical_products_owner_user_id", table_name="canonical_products"
    )
    op.drop_column("canonical_products", "owner_user_id")
