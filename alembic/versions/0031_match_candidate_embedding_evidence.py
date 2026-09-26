"""Persist bounded Product Match embedding evidence metadata."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031_match_embedding_evidence"
down_revision: str | None = "0030_store_logo_media"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "match_candidate_logs",
        sa.Column("embedding_evidence", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("match_candidate_logs", "embedding_evidence")
