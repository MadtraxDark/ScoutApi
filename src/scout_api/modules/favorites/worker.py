"""PostgreSQL-backed SMTP outbox worker."""

from __future__ import annotations

import argparse
import logging
import signal
import smtplib
import ssl
import time
import uuid
from datetime import UTC, datetime, timedelta
from email.utils import parseaddr

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from scout_api.core.config import Settings, get_settings
from scout_api.core.database import get_session_factory
from scout_api.modules.favorites.email_transport import (
    EmailTransport,
    SmtpEmailTransport,
    message_id_for_delivery,
    render_price_drop,
)
from scout_api.modules.matching.models import EmailDelivery

logger = logging.getLogger(__name__)
_STOP = False
_BACKOFF_SECONDS = (60, 300, 900, 3600)


def _handle_stop(_signum: int, _frame: object) -> None:
    global _STOP
    _STOP = True


def claim_due(
    session: Session, *, worker_id: str, settings: Settings
) -> list[EmailDelivery]:
    now = datetime.now(UTC)
    rows = list(
        session.scalars(
            select(EmailDelivery)
            .where(
                or_(
                    EmailDelivery.status.in_(("pending", "retry"))
                    & (EmailDelivery.next_attempt_at <= now),
                    (EmailDelivery.status == "sending")
                    & (EmailDelivery.claim_expires_at <= now),
                )
            )
            .order_by(EmailDelivery.created_at)
            .limit(settings.email_worker_batch_size)
            .with_for_update(skip_locked=True)
        ).all()
    )
    claimed: list[EmailDelivery] = []
    for row in rows:
        if (
            row.status == "sending"
            and row.attempts >= settings.email_worker_max_attempts
        ):
            row.status = "failed"
            row.last_error = "worker_lease_expired"
            row.claim_expires_at = None
            row.worker_id = None
            row.updated_at = now
            continue
        row.status = "sending"
        row.attempts += 1
        row.claimed_at = now
        row.claim_expires_at = now + timedelta(
            seconds=settings.email_worker_lease_seconds
        )
        row.worker_id = worker_id
        row.updated_at = now
        claimed.append(row)
    session.flush()
    return claimed


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, ssl.SSLCertVerificationError):
        return False
    if isinstance(exc, smtplib.SMTPResponseException):
        return 400 <= exc.smtp_code < 500
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        codes = [int(response[0]) for response in exc.recipients.values()]
        return bool(codes) and all(400 <= code < 500 for code in codes)
    return isinstance(
        exc,
        (
            TimeoutError,
            OSError,
            smtplib.SMTPServerDisconnected,
            smtplib.SMTPConnectError,
            smtplib.SMTPHeloError,
            smtplib.SMTPDataError,
        ),
    )


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, smtplib.SMTPResponseException):
        return f"smtp_{exc.smtp_code}"
    if isinstance(exc, TimeoutError):
        return "smtp_timeout"
    if isinstance(
        exc, (smtplib.SMTPAuthenticationError, smtplib.SMTPRecipientsRefused)
    ):
        return "smtp_permanent_rejection"
    if isinstance(exc, OSError):
        return "smtp_connection_error"
    return "smtp_delivery_error"


def process_delivery(
    session: Session,
    delivery: EmailDelivery,
    *,
    settings: Settings,
    transport: EmailTransport,
) -> str:
    started = time.perf_counter()
    domain = parseaddr(settings.email_from_address or "")[1].split("@")[-1]
    message_id = delivery.message_id or message_id_for_delivery(delivery.id, domain)
    delivery.message_id = message_id
    try:
        subject, text_body, html_body = render_price_drop(
            delivery.payload, public_app_url=settings.public_app_url
        )
        transport.send(
            recipient=delivery.recipient,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
            message_id=message_id,
        )
    except Exception as exc:  # noqa: BLE001
        now = datetime.now(UTC)
        retryable = (
            _is_retryable(exc)
            and delivery.attempts < settings.email_worker_max_attempts
        )
        delivery.status = "retry" if retryable else "failed"
        delivery.last_error = _safe_error(exc)
        delivery.next_attempt_at = now + timedelta(
            seconds=_BACKOFF_SECONDS[
                min(delivery.attempts - 1, len(_BACKOFF_SECONDS) - 1)
            ]
        )
        delivery.claim_expires_at = None
        delivery.worker_id = None
        delivery.updated_at = now
        logger.warning(
            "email_delivery_failed",
            extra={
                "delivery_id": str(delivery.id),
                "notification_id": str(delivery.notification_id),
                "product_id": delivery.payload.get("product_id"),
                "attempt": delivery.attempts,
                "error_code": delivery.last_error,
                "retry": retryable,
                "duration_ms": int((time.perf_counter() - started) * 1000),
            },
        )
        return delivery.status
    now = datetime.now(UTC)
    delivery.status = "sent"
    delivery.sent_at = now
    delivery.last_error = None
    delivery.claim_expires_at = None
    delivery.worker_id = None
    delivery.updated_at = now
    logger.info(
        "email_delivery_sent",
        extra={
            "delivery_id": str(delivery.id),
            "notification_id": str(delivery.notification_id),
            "product_id": delivery.payload.get("product_id"),
            "attempt": delivery.attempts,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        },
    )
    return delivery.status


def sweep_once(
    session: Session, *, worker_id: str, settings: Settings, transport: EmailTransport
) -> dict[str, int]:
    claimed = claim_due(session, worker_id=worker_id, settings=settings)
    session.commit()
    sent = retried = failed = 0
    for claimed_row in claimed:
        row = session.get(EmailDelivery, claimed_row.id)
        if row is None or row.worker_id != worker_id:
            continue
        result = process_delivery(session, row, settings=settings, transport=transport)
        session.commit()
        sent += result == "sent"
        retried += result == "retry"
        failed += result == "failed"
    return {"claimed": len(claimed), "sent": sent, "retry": retried, "failed": failed}


def run_forever(*, once: bool = False) -> None:
    global _STOP
    settings = get_settings()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGTERM, _handle_stop)
    if not settings.email_worker_enabled:
        logger.warning("email_worker_disabled")
        if not once:
            while not _STOP:
                time.sleep(settings.email_worker_sweep_interval_seconds)
        return
    if not settings.email_enabled:
        logger.warning("email_transport_disabled_pending_deliveries_preserved")
        if not once:
            while not _STOP:
                time.sleep(settings.email_worker_sweep_interval_seconds)
        return
    worker_id = uuid.uuid4().hex[:32]
    factory = get_session_factory()
    transport = SmtpEmailTransport(settings)
    logger.info(
        "email_worker_started",
        extra={"worker_id": worker_id, "email_enabled": settings.email_enabled},
    )
    while not _STOP:
        with factory() as session:
            try:
                summary = sweep_once(
                    session, worker_id=worker_id, settings=settings, transport=transport
                )
                if summary["claimed"]:
                    logger.info("email_worker_sweep_done", extra=summary)
            except Exception:  # noqa: BLE001
                logger.exception("email_worker_sweep_failed")
                session.rollback()
        if once:
            break
        for _ in range(settings.email_worker_sweep_interval_seconds):
            if _STOP:
                break
            time.sleep(1)
    logger.info("email_worker_stopped", extra={"worker_id": worker_id})


def main() -> None:
    parser = argparse.ArgumentParser(description="ScoutApiV2 email delivery worker")
    parser.add_argument("--once", action="store_true")
    run_forever(once=parser.parse_args().once)


if __name__ == "__main__":
    main()
