"""Monitor worker process — separate from the FastAPI HTTP server."""

from __future__ import annotations

import argparse
import logging
import signal
import time

from scout_api.core.config import get_settings
from scout_api.core.database import get_session_factory
from scout_api.modules.monitoring.claim import new_worker_id
from scout_api.modules.monitoring.service import OfferMonitorService

logger = logging.getLogger(__name__)

_STOP = False


def _handle_stop(_signum: int, _frame: object) -> None:
    global _STOP
    _STOP = True
    logger.info("monitor_worker_stop_requested")


def run_forever(*, once: bool = False) -> None:
    settings = get_settings()
    if not settings.offer_monitor_enabled:
        logger.warning("offer_monitor_disabled")
        return

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGTERM, _handle_stop)

    worker_id = new_worker_id()
    factory = get_session_factory()
    interval = max(5, int(settings.offer_monitor_sweep_interval_seconds))
    logger.info(
        "monitor_worker_started",
        extra={
            "worker_id": worker_id,
            "interval_seconds": interval,
            "batch_size": settings.offer_monitor_batch_size,
            "refresh_interval_hours": settings.offer_refresh_interval_hours,
        },
    )

    while not _STOP:
        session = factory()
        try:
            service = OfferMonitorService(session=session, worker_id=worker_id)
            summary = service.sweep_once()
            logger.info(
                "monitor_sweep_done",
                extra={
                    "claimed": summary["claimed"],
                    "processed": summary["processed"],
                    "duration_ms": summary["duration_ms"],
                    "due_remaining": summary["due_remaining"],
                },
            )
        except Exception:  # noqa: BLE001
            logger.exception("monitor_sweep_failed")
            session.rollback()
        finally:
            session.close()

        if once:
            break
        # Sleep in short slices so SIGTERM is responsive.
        for _ in range(interval):
            if _STOP:
                break
            time.sleep(1)

    logger.info("monitor_worker_stopped", extra={"worker_id": worker_id})


def main() -> None:
    parser = argparse.ArgumentParser(description="ScoutApiV2 offer monitor worker")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single sweep and exit (useful for tests/cron).",
    )
    args = parser.parse_args()
    run_forever(once=args.once)


if __name__ == "__main__":
    main()
