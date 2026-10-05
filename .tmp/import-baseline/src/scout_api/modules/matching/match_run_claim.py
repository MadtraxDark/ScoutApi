"""Claim due Product Match runs with lease + SKIP LOCKED (PostgreSQL)."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import Select, and_, or_, select, update
from sqlalchemy.orm import Session

from scout_api.core.config import Settings, get_settings
from scout_api.modules.matching.models import ProductMatchRun

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = ("pending", "running")
TERMINAL_STORE_STATUSES = frozenset({"match", "no_match", "error"})
FAILURE_CODE_WORKER_LOST = "worker_lost"
WORKER_LOST_MESSAGE = (
    "A busca foi interrompida (worker perdido ou lease expirada). "
    "Inicie uma nova busca."
)


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_worker_id() -> str:
    return uuid.uuid4().hex[:16]


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def lease_valid(run: ProductMatchRun, *, now: datetime | None = None) -> bool:
    """True when the durable lease is still held by a living worker."""
    moment = now or utcnow()
    expires = _as_utc(run.claim_expires_at)
    if expires is None:
        return False
    return expires > moment


def is_effectively_active(run: ProductMatchRun, *, now: datetime | None = None) -> bool:
    """User-visible / API-active: pending wait, or running with a valid lease.

    ``status=running`` alone does **not** prove a worker is alive.
    """
    status = (run.status or "").lower()
    if status == "pending":
        return True
    if status == "running":
        return lease_valid(run, now=now)
    return False


def is_stale_running(run: ProductMatchRun, *, now: datetime | None = None) -> bool:
    return (run.status or "").lower() == "running" and not lease_valid(run, now=now)


def _due_filter(*, now: datetime) -> object:
    """Pending never claimed, or running/pending with expired lease."""
    lease_expired = or_(
        ProductMatchRun.claim_expires_at.is_(None),
        ProductMatchRun.claim_expires_at <= now,
    )
    pending_unclaimed = and_(
        ProductMatchRun.status == "pending",
        lease_expired,
    )
    # Stale RUNNING with expired lease → recoverable by another worker.
    stale_running = and_(
        ProductMatchRun.status == "running",
        lease_expired,
    )
    return or_(pending_unclaimed, stale_running)


def claim_due_match_runs(
    session: Session,
    *,
    worker_id: str,
    limit: int | None = None,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> list[ProductMatchRun]:
    """Atomically claim up to ``limit`` match runs needing execution."""
    cfg = settings or get_settings()
    moment = now or utcnow()
    batch = max(1, int(limit or cfg.match_run_batch_size))
    lease = timedelta(seconds=max(60, int(cfg.match_run_lease_seconds)))
    claim_expires = moment + lease

    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        return _claim_postgres(
            session,
            worker_id=worker_id,
            batch=batch,
            now=moment,
            claim_expires=claim_expires,
        )
    return _claim_portable(
        session,
        worker_id=worker_id,
        batch=batch,
        now=moment,
        claim_expires=claim_expires,
    )


def _claim_postgres(
    session: Session,
    *,
    worker_id: str,
    batch: int,
    now: datetime,
    claim_expires: datetime,
) -> list[ProductMatchRun]:
    stmt: Select[tuple[ProductMatchRun]] = (
        select(ProductMatchRun)
        .where(_due_filter(now=now))
        .order_by(ProductMatchRun.started_at.asc())
        .limit(batch)
        .with_for_update(skip_locked=True)
    )
    rows = list(session.scalars(stmt).all())
    claimed: list[ProductMatchRun] = []
    for run in rows:
        previous_attempts = int(run.attempts or 0)
        was_stale = (run.status or "").lower() == "running"
        run.status = "running"
        run.attempts = previous_attempts + 1
        run.claimed_at = now
        run.claim_expires_at = claim_expires
        run.worker_id = worker_id
        run.last_activity_at = now
        if run.started_at is None:
            run.started_at = now
        claimed.append(run)
        event = (
            "match_job_reclaimed"
            if was_stale and previous_attempts > 0
            else "match_job_claimed"
        )
        logger.info(
            event,
            extra={
                "run_id": str(run.id),
                "worker_id": worker_id,
                "attempt": run.attempts,
                "claim_expires_at": claim_expires.isoformat(),
            },
        )
    if claimed:
        session.flush()
    return claimed


def _claim_portable(
    session: Session,
    *,
    worker_id: str,
    batch: int,
    now: datetime,
    claim_expires: datetime,
) -> list[ProductMatchRun]:
    """Optimistic claim for SQLite tests (no SKIP LOCKED)."""
    stmt = (
        select(ProductMatchRun)
        .where(_due_filter(now=now))
        .order_by(ProductMatchRun.started_at.asc())
        .limit(batch)
    )
    rows = list(session.scalars(stmt).all())
    claimed: list[ProductMatchRun] = []
    for run in rows:
        result = session.execute(
            update(ProductMatchRun)
            .where(
                ProductMatchRun.id == run.id,
                _due_filter(now=now),
            )
            .values(
                status="running",
                attempts=int(run.attempts or 0) + 1,
                claimed_at=now,
                claim_expires_at=claim_expires,
                worker_id=worker_id,
                last_activity_at=now,
            )
        )
        if result.rowcount:
            session.refresh(run)
            claimed.append(run)
    return claimed


def heartbeat_claim(
    session: Session,
    run: ProductMatchRun,
    *,
    worker_id: str,
    settings: Settings | None = None,
    now: datetime | None = None,
    expected_attempts: int | None = None,
) -> bool:
    """Extend lease while the match is still running. Returns False if lost.

    Ownership fencing: ``worker_id`` (+ optional ``attempts`` generation) must
    still match; a zombie worker cannot renew after reclaim.
    """
    cfg = settings or get_settings()
    moment = now or utcnow()
    lease = timedelta(seconds=max(60, int(cfg.match_run_lease_seconds)))
    claim_expires = moment + lease
    predicates = [
        ProductMatchRun.id == run.id,
        ProductMatchRun.worker_id == worker_id,
        ProductMatchRun.status == "running",
    ]
    if expected_attempts is not None:
        predicates.append(ProductMatchRun.attempts == int(expected_attempts))
    result = session.execute(
        update(ProductMatchRun)
        .where(*predicates)
        .values(
            claim_expires_at=claim_expires,
            last_activity_at=moment,
        )
    )
    if result.rowcount:
        run.claim_expires_at = claim_expires
        run.last_activity_at = moment
        logger.debug(
            "match_job_heartbeat",
            extra={
                "run_id": str(run.id),
                "worker_id": worker_id,
                "claim_expires_at": claim_expires.isoformat(),
            },
        )
        return True
    return False


def release_claim(run: ProductMatchRun) -> None:
    run.worker_id = None
    run.claimed_at = None
    run.claim_expires_at = None


def list_stale_running(
    session: Session,
    *,
    now: datetime | None = None,
    limit: int = 50,
) -> list[ProductMatchRun]:
    """RUNNING rows whose lease has expired (orphaned / reclaimable)."""
    moment = now or utcnow()
    stmt = (
        select(ProductMatchRun)
        .where(
            ProductMatchRun.status == "running",
            or_(
                ProductMatchRun.claim_expires_at.is_(None),
                ProductMatchRun.claim_expires_at <= moment,
            ),
        )
        .order_by(ProductMatchRun.started_at.asc())
        .limit(max(1, limit))
    )
    return list(session.scalars(stmt).all())
