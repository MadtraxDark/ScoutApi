"""durable progressive match observations

Revision ID: 0032_match_live
Revises: 0031_match_embedding_evidence
Create Date: 2026-10-05 21:17:44.708228

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0032_match_live"
down_revision: str | None = "0031_match_embedding_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "match_store_runs",
        sa.Column("matched_decision", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "match_store_runs", sa.Column("matched_payload", sa.JSON(), nullable=True)
    )
    op.add_column(
        "product_match_runs", sa.Column("reference_payload", sa.JSON(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("product_match_runs", "reference_payload")
    op.drop_column("match_store_runs", "matched_payload")
    op.drop_column("match_store_runs", "matched_decision")
