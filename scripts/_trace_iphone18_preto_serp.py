"""Trace why Preto ASIN is missing from amazon_br match for iPhone 18 Pro Max."""

from __future__ import annotations

from decimal import Decimal

from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
    get_shared_html_fetcher,
)
from scout_api.modules.matching.engine import MatchingEngine
from scout_api.modules.matching.identity import (
    build_search_queries,
    identity_from_price_item,
    identity_reference_item,
    serp_candidate_text,
)
from scout_api.modules.matching.product_match_service import _serp_title_reject_reason
from scout_api.modules.matching.store_search_service import StoreSearchService

TITLE = (
    "iPhone 18 Pro Max Apple 2TB, Câmera de 48MP, A20 Pro, "
    'Tela 6.9" Super Retina XDR, Preto'
)
TARGET = "B0HJBCQ9B7"


def main() -> None:
    ref_item = identity_reference_item(
        TITLE, brand="Apple", model="iPhone 18 Pro Max", category="smartphone"
    ).model_copy(update={"price": Decimal("21000"), "currency": "BRL"})
    ref = identity_from_price_item(ref_item)
    print("queries:", build_search_queries(ref)[:8])
    print(
        "ref color",
        ref.variant_attrs.get("color"),
        "storage",
        ref.variant_attrs.get("storage"),
    )

    search = StoreSearchService(fetcher=get_shared_html_fetcher())
    scrape = ProductScrapeService(fetcher=get_shared_html_fetcher())
    engine = MatchingEngine()

    seen: set[str] = set()
    for query in build_search_queries(ref)[:6]:
        print(f"\n=== query {query!r} ===")
        try:
            cands = search.search("amazon_br", query, limit=8)
        except Exception as exc:  # noqa: BLE001
            print("search error", exc)
            continue
        for c in cands:
            asin = (c.product_id or "").upper()
            text = serp_candidate_text(c)
            reject = _serp_title_reject_reason(ref, title=text)
            marker = " <<<< TARGET" if asin == TARGET else ""
            print(
                f"  SERP {asin} prefilter={reject or 'pass'} "
                f"title={(text or '')[:90]}{marker}"
            )
            if not asin or asin in seen:
                continue
            if reject:
                continue
            seen.add(asin)
            try:
                product = scrape.scrape(c.url)
            except Exception as exc:  # noqa: BLE001
                print(f"    scrape FAIL {type(exc).__name__}: {exc}")
                continue
            cand = identity_from_price_item(product)
            score = engine.score(ref, cand)
            print(
                f"    scrape OK color={cand.variant_attrs.get('color')} "
                f"decision={score.decision} conf={score.confidence} "
                f"reasons={[(r.code, r.detail) for r in score.reasons]}"
            )
            if asin == TARGET or score.decision == "auto_match":
                print("    DONE")
                return


if __name__ == "__main__":
    main()
