"""Favorites, price drop notifications, and durable SMTP deliveries.

Revision ID: 0034_favorites_email_outbox
Revises: 0033_catalog_activity
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034_favorites_email_outbox"
down_revision: str | None = "0033_catalog_activity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "product_favorites",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "product_id",
            sa.Uuid(),
            sa.ForeignKey("canonical_products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "notify_price_drop", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "user_id", "product_id", name="uq_product_favorites_user_product"
        ),
    )
    op.create_index(
        "ix_product_favorites_product_created",
        "product_favorites",
        ["product_id", "created_at"],
    )
    op.create_table(
        "user_notification_preferences",
        sa.Column("user_id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column(
            "email_enabled", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.add_column(
        "user_notifications", sa.Column("favorite_id", sa.Uuid(), nullable=True)
    )
    op.add_column(
        "user_notifications", sa.Column("offer_event_id", sa.Uuid(), nullable=True)
    )
    op.create_foreign_key(
        "fk_user_notifications_favorite",
        "user_notifications",
        "product_favorites",
        ["favorite_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_user_notifications_offer_event",
        "user_notifications",
        "offer_events",
        ["offer_event_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        "uq_user_notifications_favorite_offer_event",
        "user_notifications",
        ["favorite_id", "offer_event_id"],
    )
    op.create_table(
        "email_deliveries",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "notification_id",
            sa.Uuid(),
            sa.ForeignKey("user_notifications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "favorite_id",
            sa.Uuid(),
            sa.ForeignKey("product_favorites.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "offer_event_id",
            sa.Uuid(),
            sa.ForeignKey("offer_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("recipient", sa.String(320), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("message_id", sa.String(255), nullable=True),
        sa.Column("last_error", sa.String(128), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "favorite_id",
            "offer_event_id",
            name="uq_email_deliveries_favorite_offer_event",
        ),
    )
    op.create_index(
        "ix_email_deliveries_due",
        "email_deliveries",
        ["status", "next_attempt_at", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_email_deliveries_due", table_name="email_deliveries")
    op.drop_table("email_deliveries")
    op.drop_constraint(
        "uq_user_notifications_favorite_offer_event",
        "user_notifications",
        type_="unique",
    )
    op.drop_constraint(
        "fk_user_notifications_offer_event", "user_notifications", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_user_notifications_favorite", "user_notifications", type_="foreignkey"
    )
    op.drop_column("user_notifications", "offer_event_id")
    op.drop_column("user_notifications", "favorite_id")
    op.drop_table("user_notification_preferences")
    op.drop_index(
        "ix_product_favorites_product_created", table_name="product_favorites"
    )
    op.drop_table("product_favorites")
