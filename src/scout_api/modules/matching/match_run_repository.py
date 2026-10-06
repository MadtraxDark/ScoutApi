"""Repository for durable Product Match runs and notifications."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, load_only, selectinload

from scout_api.modules.matching.models import (
    ACTIVE_MATCH_RUN_STATUSES,
    CanonicalProduct,
    MatchCandidateLog,
    MatchStoreRun,
    ProductMatchRun,
    UserNotification,
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class MatchRunRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, run_id: uuid.UUID) -> ProductMatchRun | None:
        return self._session.get(ProductMatchRun, run_id)

    def get_live_snapshot(self, run_id: uuid.UUID) -> list[Any]:
        run_fields = [
            getattr(ProductMatchRun, name)
            for name in (
                "id",
                "product_id",
                "status",
                "started_at",
                "finished_at",
                "last_activity_at",
                "claimed_at",
                "claim_expires_at",
                "attempts",
                "total_duration_ms",
                "stores_total",
                "stores_completed",
                "matches_found",
                "no_matches",
                "errors",
                "failure_code",
                "failure_message",
            )
        ]
        store_fields = [
            getattr(MatchStoreRun, name).label(name)
            for name in (
                "id",
                "store",
                "store_display_name",
                "status",
                "started_at",
                "finished_at",
                "matched_decision",
                "matched_listing_id",
                "matched_url",
                "matched_title",
                "matched_price",
                "matched_currency",
                "matched_confidence",
                "error_code",
            )
        ]
        identity = [
            MatchStoreRun.matched_payload["product"][field].as_string().label(label)
            for field, label in (
                ("store", "matched_store"),
                ("country", "matched_country"),
                ("product_id", "matched_product_id"),
                ("canonical_url", "matched_canonical_url"),
            )
        ]
        stmt = (
            select(ProductMatchRun, CanonicalProduct, *store_fields, *identity)
            .join(CanonicalProduct, CanonicalProduct.id == ProductMatchRun.product_id)
            .outerjoin(MatchStoreRun, MatchStoreRun.run_id == ProductMatchRun.id)
            .where(ProductMatchRun.id == run_id)
            .options(
                load_only(*run_fields, raiseload=True),
                load_only(
                    CanonicalProduct.id, CanonicalProduct.owner_user_id, raiseload=True
                ),
            )
            .order_by(MatchStoreRun.store)
        )
        return list(self._session.execute(stmt).all())

    def lock_claim(
        self, run_id: uuid.UUID, *, worker_id: str, attempts: int | None = None
    ) -> ProductMatchRun | None:
        stmt = select(ProductMatchRun).where(
            ProductMatchRun.id == run_id,
            ProductMatchRun.worker_id == worker_id,
            ProductMatchRun.status == "running",
        )
        if attempts is not None:
            stmt = stmt.where(ProductMatchRun.attempts == attempts)
        return self._session.scalars(
            stmt.with_for_update().execution_options(populate_existing=True)
        ).first()

    def get_with_details(self, run_id: uuid.UUID) -> ProductMatchRun | None:
        stmt = (
            select(ProductMatchRun)
            .where(ProductMatchRun.id == run_id)
            .options(
                selectinload(ProductMatchRun.store_runs).selectinload(
                    MatchStoreRun.candidates
                )
            )
        )
        return self._session.scalars(stmt).first()

    def get_status_active_for_product(
        self, product_id: uuid.UUID
    ) -> ProductMatchRun | None:
        """Row with status pending|running (ignores lease — unique-index holder)."""
        stmt = (
            select(ProductMatchRun)
            .where(
                ProductMatchRun.product_id == product_id,
                ProductMatchRun.status.in_(ACTIVE_MATCH_RUN_STATUSES),
            )
            .order_by(ProductMatchRun.started_at.desc())
            .limit(1)
        )
        return self._session.scalars(stmt).first()

    def get_active_for_product(
        self,
        product_id: uuid.UUID,
        *,
        now: datetime | None = None,
    ) -> ProductMatchRun | None:
        """Effectively active run: pending, or running with a valid lease."""
        from scout_api.modules.matching.match_run_claim import is_effectively_active

        row = self.get_status_active_for_product(product_id)
        if row is None:
            return None
        if is_effectively_active(row, now=now):
            return row
        return None

    def terminal_store_keys(self, run_id: uuid.UUID) -> set[str]:
        stmt = select(MatchStoreRun.store, MatchStoreRun.status).where(
            MatchStoreRun.run_id == run_id
        )
        keys: set[str] = set()
        for store, status in self._session.execute(stmt).all():
            if (status or "").lower() in ("match", "no_match", "error"):
                keys.add(str(store).lower())
        return keys

    def list_for_product(
        self,
        product_id: uuid.UUID,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> list[ProductMatchRun]:
        stmt = (
            select(ProductMatchRun)
            .where(ProductMatchRun.product_id == product_id)
            .order_by(ProductMatchRun.started_at.desc())
            .offset(max(0, offset))
            .limit(max(1, min(limit, 100)))
        )
        return list(self._session.scalars(stmt).all())

    def create_pending(
        self,
        *,
        product_id: uuid.UUID,
        requested_by: uuid.UUID | None,
        reference_url: str | None,
    ) -> ProductMatchRun:
        now = _utcnow()
        run = ProductMatchRun(
            product_id=product_id,
            status="pending",
            requested_by=requested_by,
            reference_url=reference_url,
            started_at=now,
            created_at=now,
            updated_at=now,
            last_activity_at=now,
        )
        self._session.add(run)
        self._session.flush()
        return run

    def get_or_create_store_run(
        self,
        run_id: uuid.UUID,
        store: str,
        *,
        display_name: str | None = None,
    ) -> MatchStoreRun:
        stmt = select(MatchStoreRun).where(
            MatchStoreRun.run_id == run_id,
            MatchStoreRun.store == store,
        )
        existing = self._session.scalars(stmt).first()
        if existing is not None:
            if display_name and not existing.store_display_name:
                existing.store_display_name = display_name
            return existing
        row = MatchStoreRun(
            run_id=run_id,
            store=store,
            store_display_name=display_name,
            status="running",
            started_at=_utcnow(),
            queries=[],
            matched_reasons=[],
        )
        self._session.add(row)
        self._session.flush()
        return row

    def add_candidate_log(
        self,
        store_run: MatchStoreRun,
        *,
        sequence: int,
        title: str | None,
        url: str | None,
        store_product_id: str | None,
        decision: str | None,
        confidence: Any,
        reasons: list[Any],
        duration_ms: int | None,
        embedding_evidence: dict[str, Any] | None = None,
    ) -> MatchCandidateLog:
        # Cap evidence size — keep last 40 candidates per store.
        if len(store_run.candidates) >= 40:
            return store_run.candidates[-1]
        row = MatchCandidateLog(
            store_run_id=store_run.id,
            sequence=sequence,
            title=(title or "")[:512] or None,
            url=url,
            store_product_id=store_product_id,
            decision=decision,
            confidence=confidence,
            reasons=list(reasons or [])[:20],
            embedding_evidence=embedding_evidence,
            duration_ms=duration_ms,
        )
        self._session.add(row)
        store_run.candidates.append(row)
        return row


class NotificationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create_idempotent(
        self,
        *,
        user_id: uuid.UUID,
        type: str,
        title: str,
        message: str,
        product_id: uuid.UUID | None = None,
        match_run_id: uuid.UUID | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> UserNotification | None:
        """Insert notification; return None if unique (match_run_id, type) exists."""
        if match_run_id is not None:
            existing = self._session.scalars(
                select(UserNotification).where(
                    UserNotification.match_run_id == match_run_id,
                    UserNotification.type == type,
                )
            ).first()
            if existing is not None:
                return existing

        row = UserNotification(
            user_id=user_id,
            type=type,
            title=title[:256],
            message=message,
            product_id=product_id,
            match_run_id=match_run_id,
            metadata_json=dict(metadata or {}),
            created_at=_utcnow(),
        )
        try:
            with self._session.begin_nested():
                self._session.add(row)
                self._session.flush()
        except Exception:
            if match_run_id is not None:
                return self._session.scalars(
                    select(UserNotification).where(
                        UserNotification.match_run_id == match_run_id,
                        UserNotification.type == type,
                    )
                ).first()
            raise
        return row

    def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        limit: int = 30,
        offset: int = 0,
        unread_only: bool = False,
    ) -> list[UserNotification]:
        stmt = select(UserNotification).where(UserNotification.user_id == user_id)
        if unread_only:
            stmt = stmt.where(UserNotification.read_at.is_(None))
        stmt = (
            stmt.order_by(UserNotification.created_at.desc())
            .offset(max(0, offset))
            .limit(max(1, min(limit, 100)))
        )
        return list(self._session.scalars(stmt).all())

    def unread_count(self, user_id: uuid.UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(UserNotification)
            .where(
                UserNotification.user_id == user_id,
                UserNotification.read_at.is_(None),
            )
        )
        return int(self._session.scalar(stmt) or 0)

    def get(self, notification_id: uuid.UUID) -> UserNotification | None:
        return self._session.get(UserNotification, notification_id)

    def mark_read(
        self, notification: UserNotification, *, now: datetime | None = None
    ) -> UserNotification:
        if notification.read_at is None:
            notification.read_at = now or _utcnow()
            self._session.flush()
        return notification

    def mark_all_read(self, user_id: uuid.UUID, *, now: datetime | None = None) -> int:
        moment = now or _utcnow()
        rows = self._session.scalars(
            select(UserNotification).where(
                UserNotification.user_id == user_id,
                UserNotification.read_at.is_(None),
            )
        ).all()
        for row in rows:
            row.read_at = moment
        self._session.flush()
        return len(rows)
