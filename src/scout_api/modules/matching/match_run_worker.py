"""Background Product Match run worker (PostgreSQL-backed durable queue)."""

from __future__ import annotations

import argparse
import logging
import signal
import threading
import time
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from scout_api.core.config import Settings, get_settings
from scout_api.core.database import get_session_factory
from scout_api.core.log_redaction import install_log_redaction
from scout_api.core.performance import OperationCategory, timed
from scout_api.modules.crawler.core.exceptions import ParseError, RequestError
from scout_api.modules.matching.embedding_evidence import (
    MatchEmbeddingEvaluator,
    MatchEmbeddingRuntime,
)
from scout_api.modules.matching.identity import identity_reference_item
from scout_api.modules.matching.match_deadlines import MonotonicDeadline
from scout_api.modules.matching.match_hang_constants import (
    FAILURE_CODE_RUN_WALL_TIMEOUT,
)
from scout_api.modules.matching.match_progress import (
    GLOBAL_MATCH_PROGRESS,
    MatchProgressPhase,
)
from scout_api.modules.matching.match_run_claim import (
    FAILURE_CODE_WORKER_LOST,
    WORKER_LOST_MESSAGE,
    claim_due_match_runs,
    heartbeat_claim,
    list_stale_running,
    new_worker_id,
    utcnow,
)
from scout_api.modules.matching.match_run_repository import MatchRunRepository
from scout_api.modules.matching.match_run_service import MatchRunService
from scout_api.modules.matching.match_run_staging import (
    restore_hit,
    restore_product,
    stage_product,
)
from scout_api.modules.matching.match_watchdog import MatchHangWatchdog
from scout_api.modules.matching.models import CanonicalProduct, ProductMatchRun
from scout_api.modules.matching.product_match_service import (
    MatchStoreOutcome,
    ProductMatchService,
)
from scout_api.modules.matching.schemas import MatchHit, MatchRequest, MatchResponse

_embedding_runtime: MatchEmbeddingRuntime | None = None

logger = logging.getLogger(__name__)

_STOP = False
_scheduler_lock = threading.Lock()
_scheduler: MatchRunScheduler | None = None
_active_watchdog: MatchHangWatchdog | None = None


def _handle_stop(_signum: int, _frame: object) -> None:
    global _STOP
    _STOP = True
    logger.info("match_run_worker_stop_requested")
    wd = _active_watchdog
    if wd is not None:
        wd.request_shutdown()


def _fail_stale_exhausted(
    session: Session,
    run: ProductMatchRun,
    *,
    settings: Settings,
) -> bool:
    """Mark irrecoverable runs that exceeded max attempts after lease expiry."""
    max_attempts = max(1, int(settings.match_run_max_attempts))
    if int(run.attempts or 0) < max_attempts:
        return False
    if run.status not in ("pending", "running"):
        return False
    MatchRunService(session).finalize_failed(
        run,
        code=FAILURE_CODE_WORKER_LOST,
        message=WORKER_LOST_MESSAGE,
    )
    return True


def reconcile_exhausted_stale_runs(
    session: Session,
    *,
    settings: Settings | None = None,
    now: object | None = None,
) -> int:
    """Terminalize RUNNING+expired-lease runs that already hit max attempts.

    Does not claim — frees the product when reclaim would only fail.
    """
    from datetime import datetime

    cfg = settings or get_settings()
    max_attempts = max(1, int(cfg.match_run_max_attempts))
    moment = now if isinstance(now, datetime) else utcnow()
    exhausted = 0
    for run in list_stale_running(session, now=moment, limit=100):
        if int(run.attempts or 0) < max_attempts:
            continue
        logger.info(
            "match_job_lease_expired",
            extra={
                "run_id": str(run.id),
                "attempt": int(run.attempts or 0),
                "claim_expires_at": (
                    run.claim_expires_at.isoformat() if run.claim_expires_at else None
                ),
            },
        )
        MatchRunService(session).finalize_failed(
            run,
            code=FAILURE_CODE_WORKER_LOST,
            message=WORKER_LOST_MESSAGE,
            now=moment,
        )
        exhausted += 1
    if exhausted:
        session.flush()
    return exhausted


def _reference_from_canonical(product: CanonicalProduct) -> object:
    """Build identity-only reference when live reference scrape fails."""
    attrs = dict(product.attributes or {})
    variant = attrs.get("variant")
    category = attrs.get("category")
    return identity_reference_item(
        product.title or "",
        brand=product.brand,
        model=product.model,
        variant=str(variant) if variant else None,
        category=str(category) if category else None,
    )


def _canonical_has_match_identity(product: CanonicalProduct | None) -> bool:
    """True when catalog title is enough to skip a live reference scrape."""
    if product is None:
        return False
    return bool((product.title or "").strip())


def _execute_match_for_run(
    session: Session,
    *,
    run_id: object,
    product_id: UUID,
    reference_url: str,
    on_store_outcome: Any,
    on_targets_resolved: Any = None,
    on_store_started: Any = None,
    before_persist: Any = None,
    skip_stores: frozenset[str] | set[str] | None = None,
    run_deadline: MonotonicDeadline | None = None,
    embedding_evaluator: MatchEmbeddingEvaluator | None = None,
    settings: Settings | None = None,
) -> MatchResponse:
    """Match using catalog identity first; live reference scrape only as fallback."""
    cfg = settings or get_settings()
    match_service_args: dict[str, Any] = {"session": session}
    if cfg.match_embeddings_mode != "off" and embedding_evaluator is not None:
        match_service_args.update(
            embedding_evaluator=embedding_evaluator,
            embedding_mode=cfg.match_embeddings_mode,
            embedding_active_threshold=cfg.match_embeddings_active_similarity_threshold,
        )
    match_service = ProductMatchService(**match_service_args)
    durable_run = session.get(ProductMatchRun, run_id)
    restored_matches: list[MatchHit] = []
    target_stores = None
    if durable_run is not None:
        target_stores = [row.store for row in durable_run.store_runs] or None
        for row in durable_run.store_runs:
            if row.status == "match":
                if not row.matched_payload:
                    raise RequestError(
                        "Resultado anterior sem contexto recuperável",
                        code="MATCH_RECOVERY_INCOMPLETE",
                    )
                restored_matches.append(restore_hit(row.matched_payload))
    durable_args = dict(
        restored_matches=restored_matches,
        target_stores=target_stores,
        on_targets_resolved=on_targets_resolved,
        on_store_started=on_store_started,
        before_persist=before_persist,
    )
    if durable_run is not None and durable_run.reference_payload:
        return match_service.match_from_item(
            restore_product(durable_run.reference_payload),
            persist=True,
            include_review=True,
            canonical_product_id=product_id,
            on_store_outcome=on_store_outcome,
            skip_stores=skip_stores,
            run_deadline=run_deadline,
            progress_tracker=GLOBAL_MATCH_PROGRESS,
            run_id=str(run_id),
            **durable_args,
        )
    product = session.get(CanonicalProduct, product_id)
    run_id_str = str(run_id)

    if _canonical_has_match_identity(product):
        assert product is not None
        logger.info(
            "match_run_identity_first_reference",
            extra={
                "run_id": run_id_str,
                "product_id": str(product_id),
                "title_len": len((product.title or "").strip()),
            },
        )
        reference = _reference_from_canonical(product)
        return match_service.match_from_item(
            reference,  # type: ignore[arg-type]
            persist=True,
            include_review=True,
            include_images=False,
            canonical_product_id=product_id,
            on_store_outcome=on_store_outcome,
            **durable_args,
            skip_stores=skip_stores,
            run_deadline=run_deadline,
            progress_tracker=GLOBAL_MATCH_PROGRESS,
            run_id=run_id_str,
        )

    request = MatchRequest(
        reference_url=reference_url,  # type: ignore[arg-type]
        canonical_product_id=product_id,
        persist=True,
        include_review=True,
        include_images=False,
    )
    try:
        return match_service.match(
            request,
            on_store_outcome=on_store_outcome,
            **durable_args,
            skip_stores=skip_stores,
            run_deadline=run_deadline,
            progress_tracker=GLOBAL_MATCH_PROGRESS,
            run_id=run_id_str,
        )
    except RequestError as exc:
        title = (product.title or "").strip() if product is not None else ""
        if not title:
            raise
        logger.warning(
            "match_run_reference_fallback",
            extra={
                "run_id": run_id_str,
                "product_id": str(product_id),
                "code": exc.code,
            },
        )
        assert product is not None
        reference = _reference_from_canonical(product)
        return match_service.match_from_item(
            reference,  # type: ignore[arg-type]
            persist=True,
            include_review=True,
            include_images=False,
            canonical_product_id=product_id,
            on_store_outcome=on_store_outcome,
            **durable_args,
            skip_stores=skip_stores,
            run_deadline=run_deadline,
            progress_tracker=GLOBAL_MATCH_PROGRESS,
            run_id=run_id_str,
        )


def process_claimed_run(
    session: Session,
    run: ProductMatchRun,
    *,
    worker_id: str,
    settings: Settings,
    embedding_runtime: MatchEmbeddingRuntime | None = None,
) -> ProductMatchRun:
    """Execute one claimed match run; never raises — status is persisted."""
    run_id = run.id
    product_id = run.product_id
    reference_url = run.reference_url
    claim_attempts = int(run.attempts or 0)
    started = utcnow()
    service = MatchRunService(session)

    locked = MatchRunRepository(session).lock_claim(
        run_id, worker_id=worker_id, attempts=claim_attempts
    )
    if locked is None:
        return run
    run = locked

    if not reference_url:
        service.finalize_failed(
            run,
            code="REFERENCE_URL_MISSING",
            message="Produto sem URL de referência para busca.",
            expected_worker_id=worker_id,
            expected_attempts=claim_attempts,
        )
        session.flush()
        return run

    skip_stores = MatchRunRepository(session).terminal_store_keys(run_id)

    # Claim already committed in sweep_once. Touch activity in a short
    # transaction, then RELEASE the row lock before the long match.
    # Holding product_match_runs locked during Camoufox/search blocks
    # on_store_outcome + heartbeat (statement_timeout → INTERNAL_ERROR).
    run.status = "running"
    run.last_activity_at = utcnow()
    session.commit()
    session.expunge_all()

    run_id_str = str(run_id)
    GLOBAL_MATCH_PROGRESS.arm(run_id=run_id_str, worker_id=worker_id)
    run_deadline = MonotonicDeadline.start(
        timeout_seconds=float(settings.match_run_wall_timeout_seconds)
    )

    stop_heartbeat = threading.Event()

    def _heartbeat_loop() -> None:
        interval = max(15, int(settings.match_run_heartbeat_interval_seconds))
        while not stop_heartbeat.wait(interval):
            try:
                # Lease renewal is liveness only — MUST NOT count as progress.
                GLOBAL_MATCH_PROGRESS.note_lease_heartbeat()
                factory = get_session_factory()
                with factory() as hb_session:
                    row = hb_session.get(ProductMatchRun, run_id)
                    if row is None or row.status != "running":
                        return
                    ok = heartbeat_claim(
                        hb_session,
                        row,
                        worker_id=worker_id,
                        settings=settings,
                        expected_attempts=claim_attempts,
                    )
                    hb_session.commit()
                    if not ok:
                        return
            except Exception:  # noqa: BLE001
                logger.exception("match_run_heartbeat_failed run_id=%s", run_id)

    hb_thread = threading.Thread(
        target=_heartbeat_loop, name=f"match-hb-{run_id.hex[:8]}", daemon=True
    )
    hb_thread.start()

    # Test-only hard hang AFTER heartbeat starts (rejected in production).
    # Proves watchdog fires despite lease renewals (heartbeat ≠ progress).
    if settings.match_run_test_inject_hang:
        logger.critical(
            "match_run_test_inject_hang_armed",
            extra={"run_id": run_id_str, "worker_id": worker_id},
        )
        while not _STOP:
            time.sleep(60)

    outcome_lock = threading.Lock()

    def short_update(action: Any) -> None:
        factory = get_session_factory()
        with factory() as update_session:
            row = MatchRunRepository(update_session).lock_claim(
                run_id, worker_id=worker_id, attempts=claim_attempts
            )
            if row is None:
                raise RequestError("Attempt substituída", code="MATCH_CLAIM_LOST")
            action(MatchRunService(update_session), row)
            update_session.commit()

    def on_targets_resolved(stores: list[str], reference: Any) -> None:
        short_update(
            lambda svc, row: svc.record_targets(row, stores, stage_product(reference))
        )

    def on_store_started(store: str) -> None:
        short_update(lambda svc, row: svc.record_store_started(row, store))

    def before_persist() -> None:
        # No external work after this lock; catalog + links + terminal commit together.
        session.expire_all()
        row = MatchRunRepository(session).lock_claim(
            run_id, worker_id=worker_id, attempts=claim_attempts
        )
        if row is None:
            raise RequestError("Attempt substituída", code="MATCH_CLAIM_LOST")

    def on_store_outcome(outcome: MatchStoreOutcome) -> None:
        with outcome_lock:
            try:
                phase = MatchProgressPhase.FINALIZING_STORE
                if outcome.status == "match":
                    phase = MatchProgressPhase.MATCHING
                GLOBAL_MATCH_PROGRESS.mark_progress(
                    run_id=run_id_str,
                    store=outcome.store,
                    phase=phase,
                )
                factory = get_session_factory()
                with factory() as store_session:
                    row = store_session.get(ProductMatchRun, run_id)
                    if row is None:
                        return
                    MatchRunService(store_session).apply_store_outcome(
                        row,
                        store=outcome.store,
                        display_name=outcome.display_name,
                        status=outcome.status,
                        duration_ms=outcome.duration_ms,
                        queries=list(outcome.queries),
                        candidates_found=outcome.candidates_found,
                        candidates_evaluated=outcome.candidates_evaluated,
                        matched_decision=outcome.matched_decision,
                        matched_payload=outcome.matched_payload,
                        matched_url=outcome.matched_url,
                        matched_title=outcome.matched_title,
                        matched_price=outcome.matched_price,
                        matched_currency=outcome.matched_currency,
                        matched_confidence=outcome.matched_confidence,
                        matched_reasons=list(outcome.matched_reasons),
                        error_code=outcome.error_code,
                        error_message=outcome.error_message,
                        search_duration_ms=outcome.search_duration_ms,
                        candidate_fetch_duration_ms=outcome.candidate_fetch_duration_ms,
                        candidates=[dict(c) for c in outcome.candidates],
                        expected_worker_id=worker_id,
                        expected_attempts=claim_attempts,
                    )
                    store_session.commit()
            except Exception:  # noqa: BLE001
                logger.exception(
                    "match_run_store_outcome_failed run_id=%s store=%s",
                    run_id,
                    outcome.store,
                )
                raise

    try:
        with timed(
            "product_match_run_job",
            category=OperationCategory.PRODUCT_MATCH,
            context={
                "run_id": run_id_str,
                "product_id": str(product_id),
                "attempt": claim_attempts,
                "skip_stores": sorted(skip_stores),
            },
        ):
            execute_kwargs: dict[str, Any] = {
                "run_id": run_id,
                "product_id": product_id,
                "reference_url": reference_url,
                "on_store_outcome": on_store_outcome,
                "on_targets_resolved": on_targets_resolved,
                "on_store_started": on_store_started,
                "before_persist": before_persist,
                "skip_stores": skip_stores,
                "run_deadline": run_deadline,
            }
            if (
                settings.match_embeddings_mode != "off"
                and embedding_runtime is not None
            ):
                execute_kwargs.update(
                    embedding_evaluator=embedding_runtime.new_run(),
                    settings=settings,
                )
            response = _execute_match_for_run(session, **execute_kwargs)

        GLOBAL_MATCH_PROGRESS.mark_progress(
            run_id=run_id_str,
            phase=MatchProgressPhase.FINALIZING_RUN,
        )
        session.expire_all()
        fresh = session.get(ProductMatchRun, run_id)
        if fresh is None:
            return run
        for hit in response.matches:
            for store_row in fresh.store_runs:
                if store_row.store == hit.store:
                    store_row.matched_listing_id = hit.listing_id
        matches_found = max(int(fresh.matches_found or 0), len(response.matches))
        no_matches = max(int(fresh.no_matches or 0), len(response.unmatched_stores))
        errors = max(int(fresh.errors or 0), len(response.errors))
        stores_total = max(
            int(fresh.stores_total or 0),
            matches_found + no_matches + errors,
            int(fresh.stores_completed or 0),
        )
        service.finalize_completed(
            fresh,
            matches_found=matches_found,
            no_matches=no_matches,
            errors=errors,
            stores_total=stores_total,
            stores_completed=int(fresh.stores_completed or stores_total),
            expected_worker_id=worker_id,
            expected_attempts=claim_attempts,
        )
        finished = utcnow()
        logger.info(
            "match_run_done",
            extra={
                "run_id": run_id_str,
                "product_id": str(product_id),
                "status": "completed",
                "attempt": claim_attempts,
                "started_at": started.isoformat(),
                "finished_at": finished.isoformat(),
                "duration_ms": int((finished - started).total_seconds() * 1000),
                "matches_found": matches_found,
                "no_matches": no_matches,
                "errors": errors,
            },
        )
        return fresh
    except RequestError as exc:
        session.rollback()
        fresh = session.get(ProductMatchRun, run_id)
        if fresh is None:
            return run
        code = exc.code or "REQUEST_ERROR"
        if code == FAILURE_CODE_RUN_WALL_TIMEOUT:
            logger.warning(
                "match_run_wall_timeout",
                extra={"run_id": run_id_str, "worker_id": worker_id},
            )
        service.finalize_failed(
            fresh,
            code=code,
            message=str(exc),
            expected_worker_id=worker_id,
            expected_attempts=claim_attempts,
        )
        return fresh
    except ParseError as exc:
        session.rollback()
        fresh = session.get(ProductMatchRun, run_id)
        if fresh is None:
            return run
        service.finalize_failed(
            fresh,
            code="PARSE_ERROR",
            message=str(exc),
            expected_worker_id=worker_id,
            expected_attempts=claim_attempts,
        )
        return fresh
    except Exception:  # noqa: BLE001
        logger.exception("match_run_failed run_id=%s", run_id)
        session.rollback()
        fresh = session.get(ProductMatchRun, run_id)
        if fresh is None:
            return run
        service.finalize_failed(
            fresh,
            code="INTERNAL_ERROR",
            message="Falha interna no Product Match",
            expected_worker_id=worker_id,
            expected_attempts=claim_attempts,
        )
        return fresh
    finally:
        stop_heartbeat.set()
        hb_thread.join(timeout=2.0)
        GLOBAL_MATCH_PROGRESS.disarm(run_id=run_id_str)


def sweep_once(
    session: Session,
    *,
    worker_id: str,
    settings: Settings | None = None,
    embedding_runtime: MatchEmbeddingRuntime | None = None,
) -> dict[str, Any]:
    """Reconcile exhausted stale leases, then claim and process due runs."""
    cfg = settings or get_settings()
    if not cfg.match_run_worker_enabled:
        return {"claimed": 0, "processed": 0, "duration_ms": 0, "skipped": True}

    started = time.perf_counter()
    exhausted = reconcile_exhausted_stale_runs(session, settings=cfg)
    if exhausted:
        session.commit()

    claimed = claim_due_match_runs(session, worker_id=worker_id, settings=cfg)
    session.commit()

    processed = 0
    for run in claimed:
        row = session.get(ProductMatchRun, run.id)
        if row is None:
            continue
        process_claimed_run(
            session,
            row,
            worker_id=worker_id,
            settings=cfg,
            embedding_runtime=embedding_runtime,
        )
        session.commit()
        processed += 1

    return {
        "claimed": len(claimed),
        "processed": processed,
        "exhausted": exhausted,
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }


class MatchRunScheduler:
    """In-process poller used by the API lifespan (and tests)."""

    def __init__(self, *, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._worker_id = new_worker_id()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="match-run-scheduler", daemon=True
        )
        self._thread.start()
        logger.info(
            "match_run_scheduler_started",
            extra={"worker_id": self._worker_id},
        )

    def stop(self, *, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
        logger.info("match_run_scheduler_stopped")

    def _loop(self) -> None:
        interval = max(1.0, float(self._settings.match_run_sweep_interval_seconds))
        while not self._stop.is_set():
            try:
                if self._settings.database_url:
                    factory = get_session_factory()
                    with factory() as session:
                        sweep_once(
                            session, worker_id=self._worker_id, settings=self._settings
                        )
            except Exception:  # noqa: BLE001
                logger.exception("match_run_scheduler_sweep_failed")
            self._stop.wait(interval)


def start_scheduler(*, settings: Settings | None = None) -> MatchRunScheduler | None:
    global _scheduler
    cfg = settings or get_settings()
    if not cfg.match_run_worker_enabled:
        return None
    with _scheduler_lock:
        if _scheduler is None:
            _scheduler = MatchRunScheduler(settings=cfg)
            _scheduler.start()
        return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    with _scheduler_lock:
        if _scheduler is not None:
            _scheduler.stop()
            _scheduler = None


def run_forever(*, settings: Settings | None = None) -> None:
    global _STOP, _active_watchdog, _embedding_runtime
    cfg = settings or get_settings()
    _embedding_runtime = None
    worker_id = new_worker_id()
    if cfg.match_embeddings_mode != "off":
        if (
            cfg.match_embeddings_provider == "openai_compatible"
            or cfg.match_embeddings_api_key
        ):
            try:
                _embedding_runtime = MatchEmbeddingRuntime(cfg)
                logger.info(
                    "match_embedding_runtime_ready",
                    extra={
                        "mode": cfg.match_embeddings_mode,
                        "provider": cfg.match_embeddings_provider,
                        "model": cfg.match_embeddings_model,
                        "dimensions": cfg.match_embeddings_dimensions,
                    },
                )
            except Exception:  # noqa: BLE001 - worker keeps baseline available
                _embedding_runtime = None
                logger.exception("match_embedding_runtime_unavailable")
        else:
            logger.warning("match_embedding_runtime_disabled_missing_credentials")
    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGTERM, _handle_stop)
    logger.info("match_run_worker_started", extra={"worker_id": worker_id})
    watchdog = MatchHangWatchdog(
        tracker=GLOBAL_MATCH_PROGRESS,
        stale_seconds=float(cfg.match_run_watchdog_stale_seconds),
        check_interval_seconds=float(cfg.match_run_watchdog_check_interval_seconds),
        enabled=bool(cfg.match_run_watchdog_enabled),
    )
    _active_watchdog = watchdog
    watchdog.start()
    interval = max(1.0, float(cfg.match_run_sweep_interval_seconds))
    try:
        while not _STOP:
            try:
                factory = get_session_factory()
                with factory() as session:
                    sweep_once(
                        session,
                        worker_id=worker_id,
                        settings=cfg,
                        embedding_runtime=_embedding_runtime,
                    )
            except Exception:  # noqa: BLE001
                logger.exception("match_run_worker_sweep_failed")
            time.sleep(interval)
    finally:
        watchdog.request_shutdown()
        watchdog.stop()
        _active_watchdog = None
        if _embedding_runtime is not None:
            _embedding_runtime.close()
            _embedding_runtime = None
        logger.info("match_run_worker_stopped", extra={"worker_id": worker_id})


def main() -> None:
    logging.basicConfig(
        level=getattr(logging, get_settings().scraper_log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    install_log_redaction()
    parser = argparse.ArgumentParser(description="ScoutApiV2 product match run worker")
    parser.parse_args()
    run_forever()


if __name__ == "__main__":
    main()
