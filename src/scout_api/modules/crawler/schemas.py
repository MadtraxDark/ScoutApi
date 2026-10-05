from pydantic import BaseModel, Field

from scout_api.core.http_url import AbsoluteHttpUrl


class CrawlRequest(BaseModel):
    url: AbsoluteHttpUrl = Field(
        description="URL pública do produto ou da oferta na loja."
    )
    include_images: bool = Field(
        default=False,
        description=(
            "Quando true, extrai e normaliza a galeria de imagens do produto "
            "(ignorado no scraping só de oferta)."
        ),
    )


class CrawlError(BaseModel):
    code: str
    message: str
    url: str | None = None
    upstream_status: int | None = None
    retryable: bool = False
    retry_after: int | None = None
    rate_limit_scope: str | None = None
    rate_limit_policy: str | None = None


class CrawlErrorResponse(BaseModel):
    detail: CrawlError
