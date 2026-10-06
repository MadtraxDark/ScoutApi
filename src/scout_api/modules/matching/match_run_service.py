"""Start / query durable Product Match runs (async job API)."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from scout_api.modules.auth.schemas import AuthenticatedPrincipal
from scout_api.modules.crawler.stores import store_display_name
from scout_api.modules.matching.match_run_claim import (
    FAILURE_CODE_WORKER_LOST,
    WORKER_LOST_MESSAGE,
    is_effectively_active,
    is_stale_running,
)
from scout_api.modules.matching.match_run_repository import (
    MatchRunRepository,
    NotificationRepository,
)
from scout_api.modules.matching.models import (
    MatchStoreRun,
    ProductMatchRun,
    StoreListing,
    UserNotification,
)
from scout_api.modules.matching.product_registration_service import (
    ProductRegistrationService,
    can_access_product,
)
from scout_api.modules.matching.repository import MatchingRepository
from scout_api.modules.matching.schemas import MatchRunLiveView

logger = logging.getLogger(__name__)

_SENSITIVE_RE = re.compile(
    r"(authorization|cookie|proxy|password|token|secret|api[_-]?key)",
    re.IGNORECASE,
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime | None) -> datetime | None:
    """Normalize DB/SQLite naive timestamps to aware UTC for duration math."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def sanitize_error_message(message: str | None, *, max_len: int = 400) -> str | None:
    if not message:
        return None
    text = " ".join(str(message).split())
    if _SENSITIVE_RE.search(text):
        return "Erro sanitizado (detalhe omitido por segurança)."
    return text[:max_len]


def format_duration_hms(total_ms: int | None) -> str:
    if total_ms is None or total_ms < 0:
        return "00:00:00"
    total_sec = int(total_ms // 1000)
    hours, rem = divmod(total_sec, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


# Prefer stores with reliable public PDP scrape for Match Run reference.
# Low scores (Shopping China, etc.) still usable as fallback when alone.
_REFERENCE_STORE_PRIORITY: dict[str, int] = {
    "kabum": 100,
    "amazon_br": 95,
    "amazon": 90,
    "magazineluiza": 85,
    "magalu": 85,
    "pichau": 80,
    "terabyte": 80,
    "terabyteshop": 80,
    "nissei": 75,
    "bestbuy": 55,
    "shoppingchina": 25,
    "mercadolivre": 15,
    "shopee": 15,
}


def select_reference_url_for_product(session: Session, product_id: UUID) -> str | None:
    """Pick a durable listing URL for match — prefer scrape-reliable stores."""
    listings = MatchingRepository(session).list_listings_for_canonical(product_id)
    listings = [
        row
        for row in listings
        if not (
            row.store.casefold() == "synthetic"
            and (
                row.url.casefold().startswith("scout://identity/")
                or row.product_id.casefold().startswith("identity:")
            )
        )
    ]
    active = [row for row in listings if (row.status or "").lower() == "active"]
    pool = active or list(listings)

    def _score(listing: StoreListing) -> int:
        score = _REFERENCE_STORE_PRIORITY.get((listing.store or "").lower(), 40)
        if listing.canonical_url:
            score += 3
        elif listing.url:
            score += 2
        return score

    pool.sort(key=_score, reverse=True)
    for listing in pool:
        url = (listing.canonical_url or listing.url or "").strip()
        if url:
            return url
    return None


class MatchRunService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._runs = MatchRunRepository(session)
        self._notifications = NotificationRepository(session)
        self._registration = ProductRegistrationService(session)

    def get_live(
        self, run_id: UUID, *, principal: AuthenticatedPrincipal
    ) -> MatchRunLiveView:
        from scout_api.modules.exchange.conversion_service import convert_safe
        from scout_api.modules.exchange.repository import ExchangeRateRepository
        from scout_api.modules.matching.match_run_serializers import match_run_to_status
        from scout_api.modules.matching.schemas import (
            MatchRunLiveView,
            MatchStoreLiveView,
        )

        rows = self._runs.get_live_snapshot(run_id)
        if not rows or not can_access_product(rows[0][1], principal):
            raise LookupError("RUN_NOT_FOUND")
        run = rows[0][0]
        stores: list[MatchStoreLiveView] = []
        for row in rows:
            fields = dict(row._mapping)
            fields.pop("ProductMatchRun", None)
            fields.pop("CanonicalProduct", None)
            if fields.get("id") is not None:
                stores.append(MatchStoreLiveView.model_validate(fields))
        foreign = any(s.matched_currency not in (None, "BRL") for s in stores)
        rates = (
            ExchangeRateRepository(self._session).list_latest(quote_currency="BRL")
            if foreign
            else []
        )
        for store in stores:
            if store.matched_currency not in (None, "BRL"):
                converted = convert_safe(
                    store.matched_price,
                    store.matched_currency,
                    session=self._session,
                    rates=rates,
                )
                if converted:
                    store.converted_price_brl = converted.converted_amount
                    store.exchange_rate_status = str(converted.rate_status)
                    store.exchange_rate_updated_at = converted.fetched_at
        return MatchRunLiveView(
            run=match_run_to_status(run),
            is_effectively_active=is_effectively_active(run),
            auto_matches_found=sum(
                s.matched_decision == "auto_match" and s.status == "match"
                for s in stores
            ),
            stores=stores,
        )

    def start(
        self,
        product_id: UUID,
        *,
        principal: AuthenticatedPrincipal,
        now: datetime | None = None,
    ) -> tuple[ProductMatchRun, bool]:
        """Create a pending run or return the existing effectively-active one.

        Returns ``(run, created)``. A ``running`` row with an expired lease is
        terminalized as ``worker_lost`` so a new search can start (policy C).
        """
        moment = now or _utcnow()
        product = self._registration.get_product(product_id, viewer=principal)
        if product is None:
            raise LookupError("PRODUCT_NOT_FOUND")

        blocking = self._runs.get_status_active_for_product(product_id)
        if blocking is not None:
            if is_effectively_active(blocking, now=moment):
                return blocking, False
            if is_stale_running(blocking, now=moment):
                logger.info(
                    "match_job_lease_expired",
                    extra={
                        "run_id": str(blocking.id),
                        "product_id": str(product_id),
                        "attempt": int(blocking.attempts or 0),
                        "claim_expires_at": (
                            blocking.claim_expires_at.isoformat()
                            if blocking.claim_expires_at
                            else None
                        ),
                    },
                )
                self.finalize_failed(
                    blocking,
                    code=FAILURE_CODE_WORKER_LOST,
                    message=WORKER_LOST_MESSAGE,
                    now=moment,
                )
            else:
                # Defensive: non-active status-active row (should not happen).
                return blocking, False

        reference_url = select_reference_url_for_product(self._session, product_id)
        if not reference_url:
            raise ValueError("REFERENCE_URL_MISSING")

        try:
            with self._session.begin_nested():
                run = self._runs.create_pending(
                    product_id=product_id,
                    requested_by=principal.id,
                    reference_url=reference_url,
                )
            return run, True
        except IntegrityError:
            # Race: another request won the partial unique index.
            active = self._runs.get_active_for_product(product_id, now=moment)
            if active is not None:
                return active, False
            stale = self._runs.get_status_active_for_product(product_id)
            if stale is not None and is_stale_running(stale, now=moment):
                self.finalize_failed(
                    stale,
                    code=FAILURE_CODE_WORKER_LOST,
                    message=WORKER_LOST_MESSAGE,
                    now=moment,
                )
                with self._session.begin_nested():
                    run = self._runs.create_pending(
                        product_id=product_id,
                        requested_by=principal.id,
                        reference_url=reference_url,
                    )
                return run, True
            raise

    def get_active(
        self,
        product_id: UUID,
        *,
        principal: AuthenticatedPrincipal,
        now: datetime | None = None,
    ) -> ProductMatchRun | None:
        product = self._registration.get_product(product_id, viewer=principal)
        if product is None:
            raise LookupError("PRODUCT_NOT_FOUND")
        # Do not terminalize here — leave reclaim to the worker; only hide
        # lease-expired RUNNING from UX (policy C).
        return self._runs.get_active_for_product(product_id, now=now or _utcnow())

    def get_status(
        self, run_id: UUID, *, principal: AuthenticatedPrincipal
    ) -> ProductMatchRun:
        run = self._runs.get(run_id)
        if run is None:
            raise LookupError("RUN_NOT_FOUND")
        product = self._registration.get_product(run.product_id, viewer=principal)
        if product is None:
            raise LookupError("RUN_NOT_FOUND")
        return run

    def list_history(
        self,
        product_id: UUID,
        *,
        principal: AuthenticatedPrincipal,
        limit: int = 20,
        offset: int = 0,
    ) -> list[ProductMatchRun]:
        product = self._registration.get_product(product_id, viewer=principal)
        if product is None:
            raise LookupError("PRODUCT_NOT_FOUND")
        return self._runs.list_for_product(product_id, limit=limit, offset=offset)

    def get_details(
        self, run_id: UUID, *, principal: AuthenticatedPrincipal
    ) -> ProductMatchRun:
        run = self._runs.get_with_details(run_id)
        if run is None:
            raise LookupError("RUN_NOT_FOUND")
        product = self._registration.get_product(run.product_id, viewer=principal)
        if product is None:
            raise LookupError("RUN_NOT_FOUND")
        return run

    def finalize_completed(
        self,
        run: ProductMatchRun,
        *,
        matches_found: int,
        no_matches: int,
        errors: int,
        stores_total: int,
        stores_completed: int,
        expected_worker_id: str | None = None,
        expected_attempts: int | None = None,
        now: datetime | None = None,
    ) -> UserNotification | None:
        if expected_worker_id is not None:
            locked = self._runs.lock_claim(
                run.id, worker_id=expected_worker_id, attempts=expected_attempts
            )
            if locked is None:
                return None
            run = locked
        if expected_worker_id is not None and run.worker_id != expected_worker_id:
            logger.warning(
                "match_job_zombie_finalize_rejected",
                extra={
                    "run_id": str(run.id),
                    "expected_worker_id": expected_worker_id,
                    "actual_worker_id": run.worker_id,
                },
            )
            return None
        moment = now or _utcnow()
        # Active processing time (current claim), not wall clock including downtime.
        anchor = _as_utc(run.claimed_at) or _as_utc(run.started_at) or moment
        duration_ms = max(0, int((moment - anchor).total_seconds() * 1000))
        run.status = "completed"
        run.finished_at = moment
        run.last_activity_at = moment
        run.total_duration_ms = duration_ms
        run.matches_found = matches_found
        run.no_matches = no_matches
        run.errors = errors
        run.stores_total = stores_total
        terminal_stores = len(self._runs.terminal_store_keys(run.id))
        run.stores_completed = max(stores_completed, terminal_stores)
        run.failure_code = None
        run.failure_message = None
        run.worker_id = None
        run.claimed_at = None
        run.claim_expires_at = None
        self._session.flush()
        logger.info(
            "match_job_completed",
            extra={
                "run_id": str(run.id),
                "attempt": int(run.attempts or 0),
                "duration_ms": duration_ms,
            },
        )
        return self._notify_terminal(run, success=True)

    def finalize_failed(
        self,
        run: ProductMatchRun,
        *,
        code: str,
        message: str,
        expected_worker_id: str | None = None,
        expected_attempts: int | None = None,
        now: datetime | None = None,
    ) -> UserNotification | None:
        if expected_worker_id is not None:
            locked = self._runs.lock_claim(
                run.id, worker_id=expected_worker_id, attempts=expected_attempts
            )
            if locked is None:
                return None
            run = locked
        if expected_worker_id is not None and run.worker_id != expected_worker_id:
            logger.warning(
                "match_job_zombie_finalize_rejected",
                extra={
                    "run_id": str(run.id),
                    "expected_worker_id": expected_worker_id,
                    "actual_worker_id": run.worker_id,
                },
            )
            return None
        moment = now or _utcnow()
        anchor = _as_utc(run.claimed_at) or _as_utc(run.started_at) or moment
        duration_ms = max(0, int((moment - anchor).total_seconds() * 1000))
        normalized = (code or "INTERNAL_ERROR").strip()
        if normalized.upper() in {"STALE_LEASE_EXHAUSTED", "WORKER_LOST"}:
            normalized = FAILURE_CODE_WORKER_LOST
        run.status = "failed"
        run.finished_at = moment
        run.last_activity_at = moment
        run.total_duration_ms = duration_ms
        run.failure_code = normalized[:64]
        run.failure_message = sanitize_error_message(message)
        run.worker_id = None
        run.claimed_at = None
        run.claim_expires_at = None
        self._session.flush()
        if run.failure_code == FAILURE_CODE_WORKER_LOST:
            logger.info(
                "match_job_interrupted",
                extra={
                    "run_id": str(run.id),
                    "attempt": int(run.attempts or 0),
                    "failure_code": run.failure_code,
                    "duration_ms": duration_ms,
                },
            )
        return self._notify_terminal(run, success=False)

    def _notify_terminal(
        self, run: ProductMatchRun, *, success: bool
    ) -> UserNotification | None:
        if run.requested_by is None:
            return None
        product_title = ""
        try:
            canonical = MatchingRepository(self._session).get_canonical(run.product_id)
            if canonical is not None:
                product_title = canonical.title or ""
        except Exception:  # noqa: BLE001
            product_title = ""

        duration = format_duration_hms(run.total_duration_ms)
        stores = int(run.stores_total or 0)
        matches = int(run.matches_found or 0)

        if success:
            ntype = "PRODUCT_MATCH_COMPLETED"
            title = "Busca concluída"
            if matches > 0:
                message = (
                    f"{product_title or 'Produto'} — {matches} oferta(s) encontrada(s) "
                    f"em {stores} loja(s). Tempo total: {duration}."
                )
            else:
                message = (
                    f"Busca concluída sem novas ofertas em {stores} loja(s). "
                    f"Tempo total: {duration}."
                )
        else:
            ntype = "PRODUCT_MATCH_FAILED"
            if run.failure_code == FAILURE_CODE_WORKER_LOST:
                title = "Busca interrompida"
                message = (
                    f"{product_title or 'Produto'} — a execução foi interrompida "
                    f"antes da conclusão (worker perdido). Tempo ativo: {duration}."
                )
            else:
                title = "Busca em outras lojas não concluída"
                message = (
                    f"{product_title or 'Produto'} — a busca falhou. "
                    f"Tempo: {duration}. Consulte o log da execução."
                )

        return self._notifications.create_idempotent(
            user_id=run.requested_by,
            type=ntype,
            title=title,
            message=message,
            product_id=run.product_id,
            match_run_id=run.id,
            metadata={
                "matches_found": matches,
                "stores_total": stores,
                "duration_ms": run.total_duration_ms,
                "status": run.status,
            },
        )

    def touch_activity(self, run: ProductMatchRun) -> None:
        run.last_activity_at = _utcnow()

    def apply_store_outcome(
        self,
        run: ProductMatchRun,
        *,
        store: str,
        display_name: str | None,
        status: str,
        duration_ms: int | None,
        queries: list[str],
        candidates_found: int,
        candidates_evaluated: int,
        matched_decision: str | None = None,
        matched_payload: dict[str, Any] | None = None,
        matched_url: str | None = None,
        matched_title: str | None = None,
        matched_price: Decimal | None = None,
        matched_currency: str | None = None,
        matched_confidence: Decimal | None = None,
        matched_reasons: list[Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        search_duration_ms: int | None = None,
        candidate_fetch_duration_ms: int | None = None,
        candidates: list[dict[str, Any]] | None = None,
        expected_worker_id: str | None = None,
        expected_attempts: int | None = None,
    ) -> MatchStoreRun | None:
        if expected_worker_id is not None:
            locked = self._runs.lock_claim(
                run.id, worker_id=expected_worker_id, attempts=expected_attempts
            )
            if locked is None:
                return None
            run = locked
        if expected_worker_id is not None and run.worker_id != expected_worker_id:
            logger.warning(
                "match_job_zombie_store_outcome_rejected",
                extra={
                    "run_id": str(run.id),
                    "store": store,
                    "expected_worker_id": expected_worker_id,
                    "actual_worker_id": run.worker_id,
                },
            )
            return None
        if (run.status or "").lower() != "running":
            return None
        # Do not overwrite a terminal store result from a prior attempt.
        existing = next(
            (
                row
                for row in (run.store_runs or [])
                if (row.store or "").lower() == (store or "").lower()
            ),
            None,
        )
        if existing is not None and (existing.status or "").lower() in (
            "match",
            "no_match",
            "error",
        ):
            return existing

        store_run = self._runs.get_or_create_store_run(
            run.id, store, display_name=display_name or store_display_name(store)
        )
        now = _utcnow()
        if store_run.started_at is None:
            store_run.started_at = now - timedelta(milliseconds=duration_ms or 0)
        store_run.finished_at = now
        store_run.status = status
        store_run.duration_ms = duration_ms
        store_run.queries = list(queries or [])[:20]
        store_run.queries_count = len(store_run.queries)
        store_run.candidates_found = candidates_found
        store_run.candidates_evaluated = candidates_evaluated
        store_run.matched_url = matched_url
        store_run.matched_decision = matched_decision
        store_run.matched_payload = matched_payload
        store_run.matched_title = (matched_title or "")[:512] or None
        store_run.matched_price = matched_price
        store_run.matched_currency = matched_currency
        store_run.matched_confidence = matched_confidence
        store_run.matched_reasons = list(matched_reasons or [])[:20]
        store_run.error_code = (error_code or "")[:64] or None
        store_run.error_message = sanitize_error_message(error_message)
        store_run.search_duration_ms = search_duration_ms
        store_run.candidate_fetch_duration_ms = candidate_fetch_duration_ms

        if candidates:
            for idx, cand in enumerate(candidates[:40]):
                self._runs.add_candidate_log(
                    store_run,
                    sequence=idx + 1,
                    title=cand.get("title"),
                    url=cand.get("url"),
                    store_product_id=cand.get("store_product_id"),
                    decision=cand.get("decision"),
                    confidence=cand.get("confidence"),
                    reasons=list(cand.get("reasons") or []),
                    duration_ms=cand.get("duration_ms"),
                    embedding_evidence=cand.get("embedding_evidence"),
                )

        # Aggregates
        run.stores_completed = sum(
            1 for row in run.store_runs if row.status in ("match", "no_match", "error")
        )
        run.matches_found = sum(1 for row in run.store_runs if row.status == "match")
        run.no_matches = sum(1 for row in run.store_runs if row.status == "no_match")
        run.errors = sum(1 for row in run.store_runs if row.status == "error")
        run.last_activity_at = now
        self._session.flush()
        return store_run

    def record_targets(
        self, run: ProductMatchRun, stores: list[str], reference_payload: dict[str, Any]
    ) -> None:
        if run.reference_payload is None:
            run.reference_payload = reference_payload
        for store in stores:
            row = self._runs.get_or_create_store_run(
                run.id, store, display_name=store_display_name(store)
            )
            if row.finished_at is None:
                row.status = "pending"
                row.started_at = None
        run.stores_total = len(stores)
        self._session.flush()

    def record_store_started(self, run: ProductMatchRun, store: str) -> None:
        row = self._runs.get_or_create_store_run(run.id, store)
        if row.status not in ("match", "no_match", "error"):
            row.status = "running"
            row.started_at = _utcnow()
            run.last_activity_at = row.started_at
        self._session.flush()


class NotificationService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._repo = NotificationRepository(session)

    def list_for_user(
        self,
        user_id: UUID,
        *,
        limit: int = 30,
        offset: int = 0,
        unread_only: bool = False,
    ) -> list[UserNotification]:
        return self._repo.list_for_user(
            user_id, limit=limit, offset=offset, unread_only=unread_only
        )

    def unread_count(self, user_id: UUID) -> int:
        return self._repo.unread_count(user_id)

    def mark_read(self, notification_id: UUID, *, user_id: UUID) -> UserNotification:
        row = self._repo.get(notification_id)
        if row is None or row.user_id != user_id:
            raise LookupError("NOTIFICATION_NOT_FOUND")
        return self._repo.mark_read(row)

    def mark_all_read(self, user_id: UUID) -> int:
        return self._repo.mark_all_read(user_id)
