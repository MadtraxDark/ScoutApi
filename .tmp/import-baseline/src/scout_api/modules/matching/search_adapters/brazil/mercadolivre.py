"""Mercado Livre Store Search adapter — SERP only (not PDP)."""

from __future__ import annotations

import re
from typing import Any, ClassVar
from urllib.parse import quote_plus, urljoin, urlparse

from scrapy.http import Response

from scout_api.modules.crawler.core.exceptions import RequestError
from scout_api.modules.crawler.core.fingerprints import canonicalize_url
from scout_api.modules.matching.search_adapters.base import (
    EmptySearchClassification,
    SearchRequest,
)
from scout_api.modules.matching.search_candidate import SearchCandidate

_CATALOG_ID_RE = re.compile(r"/p/(MLB\d+)(?:[/?]|$)", re.I)
_ITEM_ID_RE = re.compile(r"MLB-?(\d{8,})", re.I)


class MercadoLivreSearchAdapter:
    """Candidate discovery for lista.mercadolivre.com.br."""

    store_key: ClassVar[str] = "mercadolivre"

    def build_search_request(self, query: str) -> SearchRequest:
        q = quote_plus(query.strip())
        return SearchRequest(
            url=f"https://lista.mercadolivre.com.br/{q}",
            method="GET",
            prefer_browser=False,
        )

    def parse_candidates(self, response: Response) -> list[SearchCandidate]:
        page_url = str(getattr(response, "url", "") or "")
        html = getattr(response, "text", None) or ""
        if "account-verification" in page_url.casefold() or (
            "account-verification" in html[:8_000].casefold()
            and "ui-search-layout" not in html.casefold()
        ):
            raise RequestError(
                "Mercado Livre exige sessão/browser (account-verification); "
                "o fetch tenta bypass Camoufox (Snoopy + warm) e proxy FALLBACK",
                code="AUTH_REQUIRED",
                url=page_url or None,
                retryable=True,
            )

        # Prefer titled organic cards; skip carousel/intervention ads that
        # otherwise flood the first N /p/MLB links (Norton, M365, …).
        anchors = response.css(
            "a.poly-component__title, a.ui-search-link, a[href*='/p/MLB']"
        )
        candidates: list[SearchCandidate] = []
        seen: set[str] = set()
        for anchor in anchors:
            href = (anchor.css("::attr(href)").get() or "").strip()
            if not href:
                continue
            absolute = urljoin(response.url, href)
            if "mercadolivre.com.br" not in absolute.casefold():
                continue
            if self._is_serp_noise_url(absolute):
                continue
            path = urlparse(absolute).path or ""
            if "/p/" not in path and "/MLB-" not in path.upper():
                continue
            canonical = canonicalize_url(absolute)
            if canonical in seen:
                continue
            seen.add(canonical)
            title = self._serp_anchor_title(anchor)
            product_id = None
            catalog = _CATALOG_ID_RE.search(path)
            if catalog:
                product_id = catalog.group(1).upper()
            else:
                item = _ITEM_ID_RE.search(path)
                if item:
                    product_id = f"MLB{item.group(1)}"
            candidates.append(
                SearchCandidate(
                    url=absolute,
                    title=title,
                    product_id=product_id,
                    metadata={"source": "mercadolivre-search"},
                )
            )
            if len(candidates) >= 40:
                break
        return candidates

    def classify_empty_result(self, response: Response) -> EmptySearchClassification:
        return "unknown"

    @staticmethod
    def _is_serp_noise_url(url: str) -> bool:
        folded = url.casefold()
        if "intervention_type=" in folded:
            return True
        if "#intervention" in folded or "intervention_type" in folded:
            return True
        return False

    @staticmethod
    def _serp_anchor_title(anchor: Any) -> str | None:
        title = (anchor.css("::attr(title)").get() or "").strip()
        if MercadoLivreSearchAdapter._usable_serp_title(title):
            return title
        texts = [
            t.strip()
            for t in anchor.css("::text").getall()
            if t and t.strip() and t.strip().casefold() not in {"r$", "rs"}
        ]
        joined = " ".join(texts).strip()
        if MercadoLivreSearchAdapter._usable_serp_title(joined):
            return joined
        return None

    @staticmethod
    def _usable_serp_title(text: str | None) -> bool:
        if not text or len(text.strip()) < 8:
            return False
        folded = text.strip().casefold()
        if folded in {"r$", "rs"}:
            return False
        compact = re.sub(r"\s+", "", folded)
        if re.fullmatch(r"[\d\.\,\%xoff\-semjuros]+", compact):
            return False
        letters = sum(1 for ch in folded if ch.isalpha())
        return letters >= 4
