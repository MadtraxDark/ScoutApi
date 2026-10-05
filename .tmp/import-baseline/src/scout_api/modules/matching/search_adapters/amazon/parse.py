"""Shared Amazon SERP parsing for regional search adapters."""

from __future__ import annotations

from urllib.parse import urljoin

from scrapy.http import Response

from scout_api.modules.crawler.core.fingerprints import canonicalize_url
from scout_api.modules.matching.search_adapters.base import EmptySearchClassification
from scout_api.modules.matching.search_candidate import SearchCandidate

_AMAZON_GENUINE_EMPTY_MARKERS = (
    "nenhum resultado",
    "não encontramos",
    "nao encontramos",
    "no results for",
    "did not match any products",
    "0 results for",
)


def parse_amazon_search_results(
    response: Response,
    *,
    host: str,
    source: str,
    limit: int = 10,
) -> list[SearchCandidate]:
    """Parse classic Amazon ``s-search-result`` cards into PDP candidates."""
    candidates: list[SearchCandidate] = []
    seen: set[str] = set()
    for card in response.css(
        "div[data-component-type='s-search-result'], div.s-result-item[data-asin]"
    ):
        asin = (card.attrib.get("data-asin") or "").strip()
        if not asin or len(asin) != 10:
            continue
        href = card.css(
            "h2 a::attr(href), a.a-link-normal.s-no-outline::attr(href)"
        ).get()
        if href:
            absolute = urljoin(response.url, href.strip())
        else:
            absolute = f"https://www.{host}/dp/{asin}"
        canonical = canonicalize_url(absolute)
        if canonical in seen:
            continue
        seen.add(canonical)
        title = " ".join(
            part.strip()
            for part in card.css(
                "h2 a span::text, h2 span.a-text-normal::text, h2 span::text, "
                "a.a-link-normal.s-line-clamp-2 span::text, "
                "span.a-size-medium.a-color-base.a-text-normal::text, "
                "span.a-size-base-plus.a-color-base.a-text-normal::text"
            ).getall()
            if part and part.strip()
        ) or None
        candidates.append(
            SearchCandidate(
                url=absolute,
                title=title.strip() if title else None,
                product_id=asin,
                metadata={"source": source, "asin": asin},
            )
        )
        if len(candidates) >= limit:
            break
    return candidates


def classify_amazon_empty_result(response: Response) -> EmptySearchClassification:
    """Distinguish genuine Amazon zero hits from incomplete ``/s`` shells."""
    page_url = str(response.url or "")
    if "/s" not in page_url.split("?", 1)[0]:
        return "unknown"
    folded = (response.text or "").casefold()
    genuine_empty = any(marker in folded for marker in _AMAZON_GENUINE_EMPTY_MARKERS)
    if genuine_empty:
        return "genuine_empty"
    if "data-asin" not in folded:
        return "incomplete"
    return "unknown"
