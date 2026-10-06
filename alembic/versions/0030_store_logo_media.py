"""Store logo media and AVIF optimization state."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030_store_logo_media"
down_revision: str | None = "0029_store_metadata"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("store_metadata", sa.Column("logo_mime_type", sa.String(64)))
    op.add_column("store_metadata", sa.Column("logo_original_file_id", sa.String(128)))
    op.add_column("store_metadata", sa.Column("logo_optimized_file_id", sa.String(128)))
    op.add_column(
        "store_metadata",
        sa.Column(
            "logo_processing_status",
            sa.String(24),
            nullable=False,
            server_default="ready",
        ),
    )
    op.add_column("store_metadata", sa.Column("logo_version", sa.String(36)))
    op.add_column("store_metadata", sa.Column("logo_processing_error", sa.Text()))


def downgrade() -> None:
    op.drop_column("store_metadata", "logo_processing_error")
    op.drop_column("store_metadata", "logo_version")
    op.drop_column("store_metadata", "logo_processing_status")
    op.drop_column("store_metadata", "logo_optimized_file_id")
    op.drop_column("store_metadata", "logo_original_file_id")
    op.drop_column("store_metadata", "logo_mime_type")
