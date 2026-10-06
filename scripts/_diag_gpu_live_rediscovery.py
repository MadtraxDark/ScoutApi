"""Live Search vs Match rediscovery for MSI RTX 5070 Shadow 3X OC.

Uses Visão VIP fixture as reference identity; searches KaBuM / Magalu / others
without hardcoding ground-truth URLs into the match pipeline. Ground-truth IDs
are only used for assertion / reporting.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from scout_api.modules.crawler.services.search_service import ProductSearchService
from scrapy.http import HtmlResponse, Request

from scout_api.modules.crawler.models.product import compose_product_price_item
from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
)
from scout_api.modules.crawler.spiders.paraguay.visaovip import VisaoVipSpider
from scout_api.modules.matching.engine import MatchingEngine
from scout_api.modules.matching.identity import (
    build_search_queries,
    identity_from_price_item,
)
from scout_api.modules.matching.product_match_service import ProductMatchService
from scout_api.modules.matching.schemas import MatchRequest

ROOT = Path(__file__).resolve().parents[1]


logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("diag_gpu")

FIXTURE = ROOT / "tests/fixtures/visaovip/product_available.html"
REF_URL = (
    "https://visaovip.com/prod/placas-de-video-nvidia/"
    "placa-de-video-msi-shadow-3x-oc-12gb-geforce-rtx5070-gddr7-912-v532-232/55359/"
)
# Reporting-only expected IDs (not fed into search).
KABUM_EXPECTED_ID = "777166"
MAGALU_EXPECTED_ID = "fkff6cf4a2"
STORES = ["kabum", "magazineluiza", "shopee", "amazon_us", "shoppingchina"]


def _reference_item():
    spider = VisaoVipSpider()
    response = HtmlResponse(
        REF_URL,
        body=FIXTURE.read_bytes(),
        encoding="utf-8",
        request=Request(REF_URL),
    )
    return compose_product_price_item(
        spider.extract_offer(response),
        spider.extract_details(response),
    )


def main() -> None:
    item = _reference_item()
    identity = identity_from_price_item(item)
    queries = build_search_queries(identity)
    report: dict[str, object] = {
        "reference_title": item.title,
        "identity": {
            "brand": identity.brand,
            "model": identity.model,
            "mpn": identity.mpn,
            "mpn_display": identity.mpn_display,
            "variant_attrs": identity.variant_attrs,
        },
        "queries": queries,
        "stores": {},
    }

    search = ProductSearchService()
    scrape = ProductScrapeService()
    engine = MatchingEngine()

    # --- Search-only probe (first progressive commercial query with edition) ---
    probe = next(
        (q for q in queries if "shadow" in q.casefold() and "5070" in q),
        queries[min(2, len(queries) - 1)] if queries else "",
    )
    for store in ("kabum", "magazineluiza"):
        try:
            candidates = search.search(store, probe, limit=10)
        except Exception as exc:  # noqa: BLE001 — diag script
            report["stores"][store] = {"search_error": str(exc), "probe": probe}
            continue
        rows = [
            {
                "product_id": c.product_id,
                "title": c.title,
                "url": c.url,
            }
            for c in candidates
        ]
        hit = any(
            (c.product_id or "")
            in {
                KABUM_EXPECTED_ID if store == "kabum" else MAGALU_EXPECTED_ID,
            }
            or (KABUM_EXPECTED_ID in (c.url or "") if store == "kabum" else False)
            or (
                MAGALU_EXPECTED_ID in (c.url or "")
                if store == "magazineluiza"
                else False
            )
            for c in candidates
        )
        report["stores"][store] = {
            "probe_query": probe,
            "candidate_count": len(candidates),
            "candidates": rows[:8],
            "ground_truth_in_serp": hit,
        }
        logger.info(
            "%s probe=%r candidates=%s ground_truth=%s",
            store,
            probe,
            len(candidates),
            hit,
        )

    # --- Full Product Match (Search + Match) ---
    # Inject reference via scrape mock so we don't need live Visão VIP fetch.
    class _FixedScrape(ProductScrapeService):
        def scrape(self, url: str, **kwargs):  # type: ignore[no-untyped-def]
            if "visaovip" in url:
                return item
            return scrape.scrape(url, **kwargs)

    matcher = ProductMatchService(
        scrape_service=_FixedScrape(),
        search_service=search,
    )
    response = matcher.match(
        MatchRequest(reference_url=REF_URL, stores=STORES, persist=False)
    )
    matches = []
    for m in response.matches:
        matches.append(
            {
                "store": m.store,
                "product_id": m.product_id,
                "url": m.url,
                "title": m.title,
                "decision": m.decision,
                "confidence": str(m.confidence),
                "reasons": [
                    {"code": r.code, "detail": r.detail, "score": r.score}
                    for r in (m.reasons or [])
                ],
            }
        )
    report["match"] = {
        "matches": matches,
        "unmatched_stores": list(response.unmatched_stores),
    }

    # Score known titles offline (Match-only) for explainability.
    for store, title, pid in (
        (
            "kabum",
            "Placa de Vídeo MSI RTX 5070 12G Shadow 3X OC NVIDIA GeForce 12GB GDDR7",
            KABUM_EXPECTED_ID,
        ),
        (
            "magazineluiza",
            "Placa de vídeo MSI RTX 5070 12G Shadow 3X OC NVIDIA GeForce 12GB GDDR7",
            MAGALU_EXPECTED_ID,
        ),
    ):
        from datetime import UTC, datetime
        from decimal import Decimal

        from scout_api.modules.crawler.models.product import ProductPriceItem

        cand = ProductPriceItem.model_validate(
            {
                "store": store,
                "country": "BR",
                "product_id": pid,
                "url": f"https://example.test/{pid}",
                "canonical_url": f"https://example.test/{pid}",
                "title": title,
                "brand": "MSI",
                "currency": "BRL",
                "price": Decimal("100.00"),
                "scraped_at": datetime(2026, 9, 19, tzinfo=UTC),
            }
        )
        score = engine.score(identity, identity_from_price_item(cand))
        report.setdefault("offline_match", {})[store] = {
            "decision": score.decision,
            "confidence": str(score.confidence),
            "reasons": [
                {"code": r.code, "detail": r.detail, "score": r.score}
                for r in score.reasons
            ],
        }

    out = ROOT / "scripts/_diag_gpu_live_rediscovery_out.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nWrote {out}", flush=True)


if __name__ == "__main__":
    main()
