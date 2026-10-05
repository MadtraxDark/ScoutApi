"""Sanitized public HTTP error helpers (no secrets / stack / SQL leakage)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from scout_api.core.log_redaction import redact_mapping, redact_string

logger = logging.getLogger(__name__)


def _safe_detail(detail: Any) -> Any:
    if isinstance(detail, dict):
        return redact_mapping(detail)
    if isinstance(detail, list):
        return [
            redact_mapping(item) if isinstance(item, dict) else item for item in detail
        ]
    if isinstance(detail, str):
        return redact_string(detail)
    return detail


async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    detail = _safe_detail(exc.detail)
    if not isinstance(detail, dict):
        detail = {
            "code": "HTTP_ERROR",
            "message": str(detail) if detail else "Erro",
            "retryable": False,
        }
    headers = dict(exc.headers) if exc.headers else None
    if (
        exc.status_code == 429
        and detail.get("code") in {"RATE_LIMITED", "DUPLICATE_REQUEST"}
        and "rate_limit_scope" not in detail
    ):
        # Marketplace cooldown is not an API-wide quota. Keep legacy codes.
        detail["rate_limit_policy"] = "upstream_cooldown"
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": detail},
        headers=headers,
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "detail": {
                "code": "VALIDATION_ERROR",
                "message": "Payload inválido",
                "retryable": False,
                "errors": _safe_detail(exc.errors()),
            }
        },
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(
        "Unhandled error path=%s type=%s",
        request.url.path,
        type(exc).__name__,
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": {
                "code": "INTERNAL_ERROR",
                "message": "Erro interno",
                "retryable": False,
            }
        },
    )
