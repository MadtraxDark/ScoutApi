"""Nissei Store Search adapter — SERP only (not PDP)."""

from __future__ import annotations

import re
from typing import ClassVar
from urllib.parse import quote_plus, urljoin, urlparse

from scrapy.http import Response

from scout_api.modules.crawler.core.fingerprints import canonicalize_url
from scout_api.modules.matching.search_adapters.base import (
    EmptySearchClassification,
    SearchRequest,
)
from scout_api.modules.matching.search_candidate import SearchCandidate


class NisseiSearchAdapter:
    """Candidate discovery for nissei.com Magento catalog search."""

    store_key: ClassVar[str] = "nissei"

    def build_search_request(self, query: str) -> SearchRequest:
        # Magento search without locale redirects to home. Prefer `/br/` — the
        # BR storefront ranks and lists the same PDPs used by Product Match;
        # `/py/` remains a valid Magento locale but often demotes S-series.
        return SearchRequest(
            url=(
                "https://nissei.com/br/catalogsearch/result/?"
                f"q={quote_plus(query.strip())}"
            ),
            method="GET",
            prefer_browser=False,
        )

    def parse_candidates(self, response: Response) -> list[SearchCandidate]:
        candidates: list[SearchCandidate] = []
        seen: set[str] = set()
        for link in response.css(
            "a.product-item-link, "
            "li.product-item a.product-item-photo, "
            "a.product-item-photo, "
            "ol.products a.product"
        ):
            href = link.attrib.get("href") or link.css("::attr(href)").get()
            if not href or not str(href).strip():
                continue
            absolute = urljoin(response.url, str(href).strip())
            path = (urlparse(absolute).path or "").lower()
            if "catalogsearch" in path or path.rstrip("/").endswith("/search"):
                continue
            if path in {"/py", "/py/", "/br", "/br/", "/"}:
                continue
            # Live Magento PDPs are often slug paths without .html
            # (e.g. /py/apple-iphone-17-a3258-dual).
            looks_product = (
                path.endswith(".html")
                or "/producto" in path
                or "/product" in path
                or bool(re.search(r"^/(?:py|br)/[a-z0-9][a-z0-9\-]{2,}/?$", path))
            )
            if not looks_product:
                continue
            canonical = canonicalize_url(absolute)
            if canonical in seen:
                continue
            seen.add(canonical)
            title_bits = [
                t.strip() for t in link.css("::text").getall() if t and t.strip()
            ]
            title = " ".join(title_bits) or None
            if not title:
                sibling = link.xpath(
                    "ancestor::li[contains(@class,'product-item')][1]"
                    "//a[contains(@class,'product-item-link')]"
                )
                title_bits = [
                    t.strip()
                    for t in sibling.css("::text").getall()
                    if t and t.strip()
                ]
                title = " ".join(title_bits) or None
            candidates.append(
                SearchCandidate(
                    url=absolute,
                    title=title,
                    metadata={"source": "nissei-search"},
                )
            )
            if len(candidates) >= 10:
                break
        return candidates

    def classify_empty_result(self, response: Response) -> EmptySearchClassification:
        return "unknown"
