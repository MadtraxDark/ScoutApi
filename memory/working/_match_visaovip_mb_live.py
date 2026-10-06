"""Live Visão VIP-only Product Match for motherboard regression (no URL inject)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from uuid import UUID

from scout_api.modules.crawler.core.scrape_guard import ScrapeGuard
from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
    get_shared_html_fetcher,
)
from scout_api.modules.matching.product_match_service import ProductMatchService
from scout_api.modules.matching.schemas import MatchRequest

# Kabum listing already persisted for the ASUS TUF B650M-E WIFI canonical.
REF = (
    "https://www.kabum.com.br/produto/523145/"
    "placa-mae-asus-tuf-gaming-b650m-e-wifi-amd-am5-b650-ddr5-preto-90mb1fv0-m0eay0"
)
# Ground-truth check only (must be discovered via search, never injected).
GT_MARKER = "/41749/"
CANONICAL = UUID("0cb47f6a-298b-441d-9154-e33a37d75cd5")
OUT = Path("memory/working/visaovip_mb_match_live.json")


def main() -> None:
    import os

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    # Prefer compose Postgres when running on the host.
    db_url = os.environ.setdefault(
        "DATABASE_URL",
        "postgresql+psycopg://scout:scout@127.0.0.1:5432/scoutapi",
    )
    get_shared_html_fetcher.cache_clear()
    guard = ScrapeGuard(
        url_cooldown_seconds=0,
        domain_min_interval_seconds=1,
        result_cache_ttl_seconds=0,
    )
    scrape = ProductScrapeService(guard=guard)
    engine = create_engine(db_url)
    with Session(engine) as session:
        svc = ProductMatchService(scrape_service=scrape, session=session)

        print("MATCH_VISAOVIP_ONLY", flush=True)
        t0 = time.perf_counter()
        resp = svc.match(
            MatchRequest(
                reference_url=REF,
                stores=["visaovip"],
                persist=True,
                include_review=True,
                max_candidates_per_store=8,
                canonical_product_id=CANONICAL,
            )
        )
        elapsed = round(time.perf_counter() - t0, 1)

        matches = []
        for m in resp.matches:
            prod = m.product
            matches.append(
                {
                    "store": m.store,
                    "decision": m.decision,
                    "url": prod.url,
                    "product_id": prod.product_id,
                    "title": prod.title,
                    "price": str(prod.price) if prod.price is not None else None,
                    "currency": prod.currency,
                    "search_query": m.search_query,
                    "reasons": [r.code for r in (m.reasons or [])],
                }
            )
        errors = [
            {"store": e.store, "code": e.code, "message": e.message}
            for e in resp.errors
        ]
        payload = {
            "secs": elapsed,
            "canonical_product_id": str(resp.canonical_product_id)
            if resp.canonical_product_id
            else None,
            "matches": matches,
            "unmatched": list(resp.unmatched_stores),
            "errors": errors,
            "gt_discovered": any(GT_MARKER in (m.get("url") or "") for m in matches),
            "reference_title": resp.reference.title,
            "reference_brand": resp.reference.brand,
            "reference_model": resp.reference.model,
        }
        session.commit()

    OUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str), flush=True)
    print("WROTE", OUT, flush=True)


if __name__ == "__main__":
    main()
