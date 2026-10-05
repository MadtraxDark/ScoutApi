"""Failure domain taxonomy for browser/store observability.

Identifies the structural layer of a crawler failure so logs and metrics
can be attributed correctly: infrastructure (browser launch, profile I/O,
queue saturation) vs. store capability (WAF, auth gate, parse error) vs.
individual operation (single navigation timeout, parse miss).
"""

from __future__ import annotations

import logging
from enum import StrEnum

logger = logging.getLogger(__name__)


class FailureDomain(StrEnum):
    BROWSER_INFRASTRUCTURE = "browser_infrastructure"
    STORE_CAPABILITY = "store_capability"
    OPERATION = "operation"


def log_failure(
    domain: FailureDomain,
    *,
    event: str,
    store: str | None = None,
    capability: str | None = None,
    operation: str | None = None,
    slot_id: int | None = None,
    exc: BaseException | None = None,
    level: int = logging.WARNING,
    extra: dict[str, object] | None = None,
) -> None:
    """Emit a structured log entry tagged with the failure domain."""
    payload: dict[str, object] = {"failure_domain": domain.value}
    if store is not None:
        payload["store"] = store
    if capability is not None:
        payload["capability"] = capability
    if operation is not None:
        payload["operation"] = operation
    if slot_id is not None:
        payload["slot_id"] = slot_id
    if extra:
        payload.update(extra)
    logger.log(level, event, extra=payload, exc_info=exc is not None)
