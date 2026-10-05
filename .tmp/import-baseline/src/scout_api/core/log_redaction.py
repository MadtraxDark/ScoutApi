"""Secret/PII redaction helpers for logs and public error surfaces."""

from __future__ import annotations

import logging
import re
from typing import Any

_SENSITIVE_KEY_RE = re.compile(
    r"(password|passwd|secret|token|authorization|cookie|api[_-]?key|"
    r"service[_-]?role|private[_-]?key|refresh[_-]?token|access[_-]?token|"
    r"database_url|proxy|credential|connection.?string)",
    re.IGNORECASE,
)
_BEARER_RE = re.compile(r"(Bearer\s+)(\S+)", re.IGNORECASE)
_URL_CREDS_RE = re.compile(r"://([^:/@]+):([^@/]+)@")


def redact_string(value: str) -> str:
    text = _BEARER_RE.sub(r"\1***", value)
    text = _URL_CREDS_RE.sub(r"://\1:***@", text)
    return text


def redact_mapping(data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in data.items():
        if _SENSITIVE_KEY_RE.search(str(key)):
            out[key] = "***"
        elif isinstance(value, dict):
            out[key] = redact_mapping(value)
        elif isinstance(value, str):
            out[key] = redact_string(value)
        else:
            out[key] = value
    return out


class RedactingFilter(logging.Filter):
    """Strip secrets from log records before they reach handlers."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_string(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = redact_mapping(record.args)
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    redact_string(a) if isinstance(a, str) else a for a in record.args
                )
        if hasattr(record, "__dict__"):
            for key, value in list(record.__dict__.items()):
                if _SENSITIVE_KEY_RE.search(key) and key not in {
                    "name",
                    "msg",
                    "args",
                    "levelname",
                    "levelno",
                    "pathname",
                    "filename",
                    "module",
                    "exc_info",
                    "exc_text",
                    "stack_info",
                    "lineno",
                    "funcName",
                    "created",
                    "msecs",
                    "relativeCreated",
                    "thread",
                    "threadName",
                    "processName",
                    "process",
                    "message",
                }:
                    setattr(record, key, "***")
                elif isinstance(value, str) and key == "message":
                    setattr(record, key, redact_string(value))
        return True


def install_log_redaction() -> None:
    root = logging.getLogger()
    filt = RedactingFilter()
    for handler in root.handlers:
        handler.addFilter(filt)
    root.addFilter(filt)
