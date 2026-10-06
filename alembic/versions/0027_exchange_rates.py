"""Create exchange_rate tables (ADR 0034)."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027_exchange_rates"
down_revision: str | None = "0026_image_optimization_lease"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # exchange_rate_latest — unique (base_currency, quote_currency, rate_type)
    op.create_table(
        "exchange_rate_latest",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("base_currency", sa.String(8), nullable=False),
        sa.Column("quote_currency", sa.String(8), nullable=False),
        sa.Column("rate_type", sa.String(32), nullable=False),
        sa.Column("rate", sa.Numeric(24, 12), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("source_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="fresh"),
        sa.Column(
            "consecutive_failures", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_unique_constraint(
        "uq_exchange_rate_latest_key",
        "exchange_rate_latest",
        ["base_currency", "quote_currency", "rate_type"],
    )
    op.create_index(
        "ix_exchange_rate_latest_pair",
        "exchange_rate_latest",
        ["base_currency", "quote_currency"],
    )

    # exchange_rate_observations — append-only history
    op.create_table(
        "exchange_rate_observations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("base_currency", sa.String(8), nullable=False),
        sa.Column("quote_currency", sa.String(8), nullable=False),
        sa.Column("rate_type", sa.String(32), nullable=False),
        sa.Column("rate", sa.Numeric(24, 12), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("source_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_exchange_rate_obs_pair_type",
        "exchange_rate_observations",
        ["base_currency", "quote_currency", "rate_type"],
    )
    op.create_index(
        "ix_exchange_rate_obs_observed_at",
        "exchange_rate_observations",
        ["observed_at"],
    )

    # exchange_rate_scheduler_state — singleton (id=1)
    op.create_table(
        "exchange_rate_scheduler_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("next_refresh_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_refresh_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "consecutive_failures", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("exchange_rate_scheduler_state")
    op.drop_index(
        "ix_exchange_rate_obs_observed_at", table_name="exchange_rate_observations"
    )
    op.drop_index(
        "ix_exchange_rate_obs_pair_type", table_name="exchange_rate_observations"
    )
    op.drop_table("exchange_rate_observations")
    op.drop_index("ix_exchange_rate_latest_pair", table_name="exchange_rate_latest")
    op.drop_constraint("uq_exchange_rate_latest_key", "exchange_rate_latest")
    op.drop_table("exchange_rate_latest")
