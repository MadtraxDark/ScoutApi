"""Re-match iPhone 18 Pro Max Preto against amazon_br after glacial fix."""

from __future__ import annotations

from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
    get_shared_html_fetcher,
)
from scout_api.modules.matching.identity import identity_reference_item
from scout_api.modules.matching.product_match_service import ProductMatchService
from scout_api.modules.matching.store_search_service import StoreSearchService

TITLE = (
    'iPhone 18 Pro Max Apple 2TB, Câmera de 48MP, A20 Pro, '
    'Tela 6.9" Super Retina XDR, Preto'
)


def main() -> None:
    ref = identity_reference_item(
        TITLE, brand="Apple", model="iPhone 18 Pro Max", category="smartphone"
    )
    # Seed a realistic BR price so extreme-price gate does not force review.
    ref = ref.model_copy(update={"price": 21000, "currency": "BRL"})
    fetcher = get_shared_html_fetcher()
    svc = ProductMatchService(
        scrape_service=ProductScrapeService(fetcher=fetcher),
        search_service=StoreSearchService(fetcher=fetcher),
    )

    def on_progress(event) -> None:
        print(
            event.type,
            event.store,
            event.status,
            event.message,
            getattr(event, "matched_decision", None),
        )

    resp = svc.match_from_item(
        ref,
        stores=["amazon_br"],
        persist=False,
        include_review=True,
        max_candidates_per_store=6,
        clear_reference_price=False,
        on_progress=on_progress,
    )
    print("matches", len(resp.matches))
    for hit in resp.matches:
        print(
            "HIT",
            hit.decision,
            hit.confidence,
            hit.product_id,
            (hit.title or "")[:80],
            [(r.code, r.detail) for r in hit.reasons],
        )
    print("unmatched", resp.unmatched_stores)
    print("errors", resp.errors)


if __name__ == "__main__":
    main()
