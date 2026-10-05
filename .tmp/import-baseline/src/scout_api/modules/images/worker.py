"""Background AVIF optimization worker (PostgreSQL-backed queue)."""

from __future__ import annotations

import argparse
import logging
import signal
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from scout_api.core.config import Settings, get_settings
from scout_api.core.database import get_session_factory
from scout_api.core.performance import OperationCategory, timed
from scout_api.modules.images.claim import (
    claim_due_optimizations,
    new_worker_id,
    release_claim,
    utcnow,
)
from scout_api.modules.images.drive_client import (
    DriveStorage,
    get_drive_storage,
)
from scout_api.modules.images.models import ProductImage
from scout_api.modules.images.pipeline import ImagePipeline
from scout_api.modules.images.repository import ProductImageRepository
from scout_api.modules.matching.store_logo_worker import process_store_logo_jobs

logger = logging.getLogger(__name__)

_STOP = False
_scheduler_lock = threading.Lock()
_scheduler: ImageOptimizationScheduler | None = None


def _handle_stop(_signum: int, _frame: object) -> None:
    global _STOP
    _STOP = True
    logger.info("image_optimization_worker_stop_requested")


def _build_drive(settings: Settings) -> DriveStorage:
    return get_drive_storage(settings)


def process_claimed_image(
    session: Session,
    image: ProductImage,
    *,
    drive: DriveStorage,
    settings: Settings,
) -> ProductImage:
    """Convert one claimed image; never raises — status is persisted."""
    started = utcnow()
    attempt = int(image.optimization_attempts or 0)
    product_id = image.canonical_product_id
    image_id = image.id
    original_size = image.original_size_bytes
    if image.optimized_status == "ready" and image.optimized_drive_file_id:
        release_claim(image)
        session.flush()
        return image

    pipeline = ImagePipeline(
        session,
        drive=drive,
        settings=settings,
        schedule_avif=False,
    )
    try:
        with timed(
            "avif_conversion_job",
            category=OperationCategory.EXTERNAL_TOOL,
            context={"image_id": str(image_id), "attempt": attempt},
        ):
            pipeline.optimize_now(image, already_claimed=True)
        finished = utcnow()
        duration_ms = int((finished - started).total_seconds() * 1000)
        optimized_size = image.optimized_size_bytes
        ratio = None
        if original_size and optimized_size:
            ratio = round(optimized_size / float(original_size), 4)
        logger.info(
            "image_optimization_done",
            extra={
                "image_id": str(image_id),
                "product_id": str(product_id),
                "optimization_status": image.optimized_status,
                "attempt": attempt,
                "started_at": started.isoformat(),
                "finished_at": finished.isoformat(),
                "duration_ms": duration_ms,
                "original_size": original_size,
                "optimized_size": optimized_size,
                "compression_ratio": ratio,
                "error": image.optimized_error,
            },
        )
        return image
    except Exception as exc:  # noqa: BLE001
        logger.exception("image_optimization_failed image_id=%s", image_id)
        image.optimized_status = "failed"
        image.optimized_error = str(exc)[:500]
        release_claim(image)
        session.flush()
        return image


def sweep_once(
    session: Session,
    *,
    worker_id: str,
    settings: Settings | None = None,
    drive: DriveStorage | None = None,
    executor: ThreadPoolExecutor | None = None,
    session_factory: Any | None = None,
) -> dict[str, Any]:
    """Claim due jobs and process them (optionally via thread pool)."""
    cfg = settings or get_settings()
    storage = drive or _build_drive(cfg)
    started = time.perf_counter()
    claimed = claim_due_optimizations(session, worker_id=worker_id, settings=cfg)
    session.commit()
    logo_processed = process_store_logo_jobs(session, drive=storage, settings=cfg)

    processed = 0
    if not claimed:
        return {
            "claimed": 0,
            "processed": logo_processed,
            "store_logos_processed": logo_processed,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }

    factory = session_factory or get_session_factory()

    if executor is None:
        for image in claimed:
            row = ProductImageRepository(session).get(image.id)
            if row is None:
                continue
            process_claimed_image(session, row, drive=storage, settings=cfg)
            session.commit()
            processed += 1
    else:
        futures: list[Future[None]] = []

        def _job(image_id: UUID) -> None:
            with factory() as job_session:
                row = ProductImageRepository(job_session).get(image_id)
                if row is None:
                    return
                process_claimed_image(job_session, row, drive=storage, settings=cfg)
                job_session.commit()

        for image in claimed:
            futures.append(executor.submit(_job, image.id))
        for fut in futures:
            fut.result()
            processed += 1

    return {
        "claimed": len(claimed),
        "processed": processed + logo_processed,
        "store_logos_processed": logo_processed,
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }


class ImageOptimizationScheduler:
    """In-process poller used by the API lifespan (and tests)."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        drive: DriveStorage | None = None,
        session_factory: Any | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._drive = drive
        self._session_factory = session_factory
        self._worker_id = new_worker_id()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, self._settings.image_avif_max_concurrency),
            thread_name_prefix="avif",
        )

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="image-optimization-scheduler",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "image_optimization_scheduler_started",
            extra={"worker_id": self._worker_id},
        )

    def stop(self, *, wait: bool = True) -> None:
        self._stop.set()
        self._wake.set()
        if wait and self._thread is not None:
            self._thread.join(timeout=10)
        self._executor.shutdown(wait=wait, cancel_futures=False)
        logger.info("image_optimization_scheduler_stopped")

    def notify(self) -> None:
        """Wake the poller after enqueue (post-commit)."""
        self._wake.set()

    def sweep_once(self) -> dict[str, Any]:
        factory = self._session_factory or get_session_factory()
        session = factory()
        try:
            return sweep_once(
                session,
                worker_id=self._worker_id,
                settings=self._settings,
                drive=self._drive,
                executor=self._executor,
                session_factory=factory,
            )
        finally:
            session.close()

    def sweep_once_inline(self) -> dict[str, Any]:
        """Process claimed jobs in the calling thread (tests / low load)."""
        factory = self._session_factory or get_session_factory()
        session = factory()
        try:
            return sweep_once(
                session,
                worker_id=self._worker_id,
                settings=self._settings,
                drive=self._drive,
                executor=None,
                session_factory=factory,
            )
        finally:
            session.close()

    def _loop(self) -> None:
        interval = max(1, int(self._settings.image_optimization_sweep_interval_seconds))
        while not self._stop.is_set():
            try:
                summary = self.sweep_once()
                if summary["claimed"] or summary.get("store_logos_processed"):
                    logger.info(
                        "image_optimization_sweep_done",
                        extra=summary,
                    )
            except Exception:  # noqa: BLE001
                logger.exception("image_optimization_sweep_failed")
            self._wake.wait(timeout=interval)
            self._wake.clear()


def get_scheduler() -> ImageOptimizationScheduler | None:
    return _scheduler


def start_scheduler(
    *,
    settings: Settings | None = None,
    drive: DriveStorage | None = None,
) -> ImageOptimizationScheduler | None:
    """Start the global in-process scheduler when enabled."""
    global _scheduler
    cfg = settings or get_settings()
    if not cfg.image_optimization_enabled:
        return None
    with _scheduler_lock:
        if _scheduler is None:
            _scheduler = ImageOptimizationScheduler(settings=cfg, drive=drive)
            _scheduler.start()
        return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    with _scheduler_lock:
        if _scheduler is not None:
            _scheduler.stop()
            _scheduler = None


def notify_optimizer() -> None:
    sched = get_scheduler()
    if sched is not None:
        sched.notify()


def run_forever(*, once: bool = False) -> None:
    """Dedicated process entrypoint (compose service)."""
    global _STOP
    settings = get_settings()
    if not settings.image_optimization_enabled:
        logger.warning("image_optimization_disabled")
        return

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGTERM, _handle_stop)

    worker_id = new_worker_id()
    factory = get_session_factory()
    interval = max(1, int(settings.image_optimization_sweep_interval_seconds))
    executor = ThreadPoolExecutor(
        max_workers=max(1, settings.image_avif_max_concurrency),
        thread_name_prefix="avif",
    )
    drive = _build_drive(settings)
    logger.info(
        "image_optimization_worker_started",
        extra={
            "worker_id": worker_id,
            "interval_seconds": interval,
            "concurrency": settings.image_avif_max_concurrency,
        },
    )

    while not _STOP:
        session = factory()
        try:
            summary = sweep_once(
                session,
                worker_id=worker_id,
                settings=settings,
                drive=drive,
                executor=executor,
            )
            if summary["claimed"]:
                logger.info("image_optimization_sweep_done", extra=summary)
        except Exception:  # noqa: BLE001
            logger.exception("image_optimization_sweep_failed")
            session.rollback()
        finally:
            session.close()

        if once:
            break
        for _ in range(interval):
            if _STOP:
                break
            time.sleep(1)

    executor.shutdown(wait=True, cancel_futures=False)
    logger.info("image_optimization_worker_stopped", extra={"worker_id": worker_id})


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ScoutApiV2 product image AVIF optimization worker"
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single sweep and exit (tests/cron).",
    )
    args = parser.parse_args()
    run_forever(once=args.once)


if __name__ == "__main__":
    main()
