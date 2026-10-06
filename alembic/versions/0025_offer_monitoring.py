"""Add persistent offer monitoring schedule + promotion state (ADR 0030)."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025_offer_monitoring"
down_revision: str | None = "0024_product_images"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "store_listings",
        sa.Column(
            "monitoring_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "store_listings",
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "store_listings",
        sa.Column(
            "last_successful_check_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.add_column(
        "store_listings",
        sa.Column("next_check_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "store_listings",
        sa.Column("next_regular_check_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "store_listings",
        sa.Column("last_price_changed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "store_listings",
        sa.Column(
            "last_availability_changed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "store_listings",
        sa.Column(
            "consecutive_failures",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "store_listings",
        sa.Column("last_check_error", sa.Text(), nullable=True),
    )
    op.add_column(
        "store_listings",
        sa.Column(
            "last_check_scheduled_for", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.add_column(
        "store_listings",
        sa.Column("last_check_delay_seconds", sa.Integer(), nullable=True),
    )
    op.add_column(
        "store_listings",
        sa.Column("check_claimed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "store_listings",
        sa.Column("check_claim_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "store_listings",
        sa.Column("check_worker_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "store_listings",
        sa.Column(
            "promotion_status",
            sa.String(length=32),
            nullable=False,
            server_default="none",
        ),
    )
    op.add_column(
        "store_listings",
        sa.Column("promotion_type", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "store_listings",
        sa.Column("promotion_starts_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "store_listings",
        sa.Column("promotion_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "store_listings",
        sa.Column("promotion_price", sa.Numeric(18, 4), nullable=True),
    )
    op.add_column(
        "store_listings",
        sa.Column("promotion_original_price", sa.Numeric(18, 4), nullable=True),
    )
    op.add_column(
        "store_listings",
        sa.Column(
            "promotion_conditions",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.add_column(
        "store_listings",
        sa.Column("promotion_source", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "store_listings",
        sa.Column("promotion_timezone", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "store_listings",
        sa.Column(
            "promotion_payload",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )

    # Backfill: existing active listings become due immediately on first sweep.
    op.execute(
        sa.text(
            """
            UPDATE store_listings
            SET next_check_at = CURRENT_TIMESTAMP,
                next_regular_check_at = CURRENT_TIMESTAMP,
                monitoring_enabled = TRUE
            WHERE status = 'active'
            """
        )
    )

    op.create_index(
        "ix_store_listings_monitor_due",
        "store_listings",
        ["next_check_at"],
        unique=False,
        postgresql_where=sa.text("monitoring_enabled IS TRUE AND status = 'active'"),
        sqlite_where=sa.text("monitoring_enabled = 1 AND status = 'active'"),
    )
    op.create_index(
        "ix_store_listings_promo_expires",
        "store_listings",
        ["promotion_expires_at"],
        unique=False,
        postgresql_where=sa.text("promotion_status = 'active'"),
        sqlite_where=sa.text("promotion_status = 'active'"),
    )

    op.create_table(
        "monitor_scheduler_state",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("worker_id", sa.String(length=64), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sweep_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_claimed_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "last_processed_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.execute(
        sa.text(
            "INSERT INTO monitor_scheduler_state (id, last_claimed_count, "
            "last_processed_count) VALUES (1, 0, 0)"
        )
    )


def downgrade() -> None:
    op.drop_table("monitor_scheduler_state")
    op.drop_index("ix_store_listings_promo_expires", table_name="store_listings")
    op.drop_index("ix_store_listings_monitor_due", table_name="store_listings")
    for column in (
        "promotion_payload",
        "promotion_timezone",
        "promotion_source",
        "promotion_conditions",
        "promotion_original_price",
        "promotion_price",
        "promotion_expires_at",
        "promotion_starts_at",
        "promotion_type",
        "promotion_status",
        "check_worker_id",
        "check_claim_expires_at",
        "check_claimed_at",
        "last_check_delay_seconds",
        "last_check_scheduled_for",
        "last_check_error",
        "consecutive_failures",
        "last_availability_changed_at",
        "last_price_changed_at",
        "next_regular_check_at",
        "next_check_at",
        "last_successful_check_at",
        "last_checked_at",
        "monitoring_enabled",
    ):
        op.drop_column("store_listings", column)
