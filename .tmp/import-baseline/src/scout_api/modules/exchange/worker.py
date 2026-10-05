"""Background exchange-rate refresh worker.

Mirrors the images worker pattern (ADR 0034):
- In-process scheduler started alongside the API lifespan
- Dedicated process entrypoint `scout-exchange` for standalone operation
- Sweep interval configurable (default 60s); actual refresh every 30min
"""

from __future__ import annotations

import argparse
import logging
import signal
import threading
import time
from typing import Any

from scout_api.core.config import Settings, get_settings
from scout_api.core.database import get_session_factory

logger = logging.getLogger(__name__)

_STOP = False
_scheduler_lock = threading.Lock()
_scheduler: ExchangeRateScheduler | None = None


def _handle_stop(_signum: int, _frame: object) -> None:
    global _STOP
    _STOP = True
    logger.info("exchange_rate_worker_stop_requested")


def sweep_once(*, settings: Settings | None = None) -> dict[str, Any]:
    """Check if refresh is due and run it if so.

    Wraps refresh_if_due; returns result summary or {'skipped': True}.
    Never raises.
    """
    from scout_api.modules.exchange.refresh_service import refresh_if_due  # noqa: PLC0415

    cfg = settings or get_settings()
    factory = get_session_factory()
    session = factory()
    try:
        result = refresh_if_due(session, settings=cfg)
        if result is None:
            return {"skipped": True}
        session.commit()
        return result
    except Exception as exc:  # noqa: BLE001
        logger.exception("exchange_rate_sweep_failed")
        session.rollback()
        return {"error": str(exc)}
    finally:
        session.close()


class ExchangeRateScheduler:
    """In-process poller used by the API lifespan."""

    def __init__(self, *, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="exchange-rate-scheduler",
            daemon=True,
        )
        self._thread.start()
        logger.info("exchange_rate_scheduler_started")

    def stop(self, *, wait: bool = True) -> None:
        self._stop.set()
        if wait and self._thread is not None:
            self._thread.join(timeout=10)
        logger.info("exchange_rate_scheduler_stopped")

    def sweep_once(self) -> dict[str, Any]:
        return sweep_once(settings=self._settings)

    def _loop(self) -> None:
        interval = max(1, int(self._settings.exchange_rate_sweep_interval_seconds))
        while not self._stop.is_set():
            try:
                summary = sweep_once(settings=self._settings)
                if not summary.get("skipped"):
                    logger.info("exchange_rate_sweep_done", extra=summary)
            except Exception:  # noqa: BLE001
                logger.exception("exchange_rate_loop_error")
            self._stop.wait(timeout=interval)


def get_scheduler() -> ExchangeRateScheduler | None:
    return _scheduler


def start_scheduler(*, settings: Settings | None = None) -> ExchangeRateScheduler | None:
    """Start the global in-process scheduler when exchange rates are enabled."""
    global _scheduler
    cfg = settings or get_settings()
    if not cfg.exchange_rate_enabled:
        return None
    with _scheduler_lock:
        if _scheduler is None:
            _scheduler = ExchangeRateScheduler(settings=cfg)
            _scheduler.start()
        return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    with _scheduler_lock:
        if _scheduler is not None:
            _scheduler.stop()
            _scheduler = None


def run_forever(*, once: bool = False) -> None:
    """Dedicated process entrypoint."""
    global _STOP
    settings = get_settings()
    if not settings.exchange_rate_enabled:
        logger.warning("exchange_rate_disabled")
        return

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGTERM, _handle_stop)

    interval = max(1, int(settings.exchange_rate_sweep_interval_seconds))
    logger.info(
        "exchange_rate_worker_started",
        extra={"interval_seconds": interval},
    )

    while not _STOP:
        try:
            summary = sweep_once(settings=settings)
            if not summary.get("skipped"):
                logger.info("exchange_rate_sweep_done", extra=summary)
        except Exception:  # noqa: BLE001
            logger.exception("exchange_rate_sweep_failed")

        if once:
            break
        for _ in range(interval):
            if _STOP:
                break
            time.sleep(1)

    logger.info("exchange_rate_worker_stopped")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ScoutApiV2 exchange-rate refresh worker"
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single sweep and exit.",
    )
    args = parser.parse_args()
    run_forever(once=args.once)


if __name__ == "__main__":
    main()
