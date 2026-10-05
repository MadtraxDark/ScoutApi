class CrawlerError(Exception):
    """Base crawler exception."""


class ParseError(CrawlerError):
    """The page shape is not understood; never emit fabricated prices."""


class MissingPriceError(ParseError):
    pass


class ProductUnavailable(CrawlerError):
    pass


class RequestError(CrawlerError):
    """The store response cannot be treated as a product state."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "REQUEST_ERROR",
        url: str | None = None,
        upstream_status: int | None = None,
        retryable: bool = False,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.url = url
        self.upstream_status = upstream_status
        self.retryable = retryable
        self.retry_after = retry_after


# Proxy Cost Mode FALLBACK may retry these after a direct failure.
# New preferred SERP-specific codes are included so proxy fallback still
# triggers when they are emitted instead of the legacy UPSTREAM_BLOCKED.
PROXY_FALLBACK_ERROR_CODES: frozenset[str] = frozenset(
    {
        "UPSTREAM_BLOCKED",        # legacy — alias; still recognised on read
        "UPSTREAM_WAF_BLOCKED",    # preferred: WAF/challenge blocks SERP fetch
        "SEARCH_INCOMPLETE_RESPONSE",  # preferred: SERP arrived but unparseable
        "AUTH_REQUIRED",
    }
)

# Structural Camoufox launch / circuit / queue — never Product Match NO_MATCH.
# Keep in sync with browser_health.BROWSER_INFRASTRUCTURE_ERROR_CODES.
BROWSER_INFRASTRUCTURE_ERROR_CODES: frozenset[str] = frozenset(
    {
        "BROWSER_LAUNCH_ERROR",
        "BROWSER_INFRASTRUCTURE_UNAVAILABLE",
        "BROWSER_QUEUE_SATURATED",
        "BROWSER_QUEUE_TIMEOUT",
        "BROWSER_JOB_CANCELLED",
    }
)

SHOPEE_AUTH_REQUIRED_MESSAGE = (
    "Shopee exige login/sessão autenticada (falta de login). "
    "Configure SHOPEE_AUTH_EMAIL e SHOPEE_AUTH_PASSWORD "
    "ou execute make seed-shopee-login."
)


def shopee_auth_required_error(*, url: str | None = None) -> RequestError:
    """Operator-facing error when Shopee blocks for missing login/session."""
    return RequestError(
        SHOPEE_AUTH_REQUIRED_MESSAGE,
        code="AUTH_REQUIRED",
        url=url,
        upstream_status=401,
        retryable=True,
    )
