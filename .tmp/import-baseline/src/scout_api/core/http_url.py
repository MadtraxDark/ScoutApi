"""HTTP URL coercion for public API request payloads."""

from __future__ import annotations

from typing import Annotated, Any
from urllib.parse import urlsplit

from pydantic import BeforeValidator, HttpUrl


def coerce_absolute_http_url(value: Any) -> Any:
    """Accept host/path pastes without scheme by defaulting to ``https://``.

    Users (and some store UIs) often copy ``mercadolivre.com.br/...`` without
    the scheme. Pydantic ``HttpUrl`` rejects those as relative URLs; prepending
    ``https://`` is safe for public product pages we crawl.
    """
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return text

    parsed = urlsplit(text)
    if parsed.scheme:
        return text
    if text.startswith("//"):
        return f"https:{text}"
    # Bare host or host/path (optionally with query/fragment).
    return f"https://{text.lstrip('/')}"


AbsoluteHttpUrl = Annotated[HttpUrl, BeforeValidator(coerce_absolute_http_url)]
