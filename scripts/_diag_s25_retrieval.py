"""Search-only diagnostic: progressive queries for long smartphone titles.

Compares candidate retrieval per eligible store without full Camoufox scrapes.
Writes JSON under data/live-match-reports/.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from scout_api.modules.crawler.models.product import ProductPriceItem
from scout_api.modules.crawler.services.store_resolver import eligible_match_store_keys
from scout_api.modules.matching.identity import (
    build_search_queries,
    identity_from_price_item,
    model_search_phrase,
)
from scout_api.modules.matching.store_search_service import StoreSearchService

ROOT = Path(__file__).resolve().parents[1]


TITLE = (
    "Celular Samsung Galaxy S25 Ultra 5G 256GB Galaxy AI Titânio Preto "
    '6,9" 12GB RAM Câm. Quádrupla 200+50+10+50MP Bateria 5000mAh Dual Chip'
)

# Historical broken ladder (series missing) — for before/after comparison.
LEGACY_BROKEN_QUERIES = [
    "samsung 256gb",
    "samsung 256gb titanio",
    "samsung 256gb titanium",
]


def _item(title: str) -> ProductPriceItem:
    return ProductPriceItem.model_validate(
        {
            "store": "magazineluiza",
            "country": "BR",
            "product_id": "diag-s25",
            "url": "https://example.com/s25",
            "canonical_url": "https://example.com/s25",
            "title": title,
            "brand": "Samsung",
            "currency": "BRL",
            "price": Decimal("1"),
            "scraped_at": datetime.now(UTC),
        }
    )


def _looks_like_target(title: str | None) -> bool:
    text = (title or "").casefold()
    return "s25" in text and "ultra" in text and "256" in text


def main() -> None:
    identity = identity_from_price_item(_item(TITLE))
    queries = build_search_queries(identity)
    stores = list(eligible_match_store_keys())
    search = StoreSearchService()
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "title": TITLE,
        "identity": {
            "brand": identity.brand,
            "model": identity.model,
            "variant_attrs": dict(identity.variant_attrs),
            "series_phrase": model_search_phrase(
                model=identity.model, title=identity.title
            ),
        },
        "queries": queries,
        "legacy_broken_queries": LEGACY_BROKEN_QUERIES,
        "stores": {},
    }

    for store in stores:
        store_row: dict[str, Any] = {"queries": [], "legacy": []}
        for query in queries[:4]:
            t0 = time.perf_counter()
            try:
                candidates = search.search(store, query, limit=5)
                err = None
            except Exception as exc:  # noqa: BLE001 — diagnostic
                candidates = []
                err = f"{type(exc).__name__}: {exc}"
            ms = (time.perf_counter() - t0) * 1000
            titles = [(c.title or "")[:120] for c in candidates]
            store_row["queries"].append(
                {
                    "query": query,
                    "ms": round(ms, 1),
                    "count": len(candidates),
                    "target_hit": any(_looks_like_target(t) for t in titles),
                    "titles": titles,
                    "error": err,
                }
            )
        for query in LEGACY_BROKEN_QUERIES[:2]:
            t0 = time.perf_counter()
            try:
                candidates = search.search(store, query, limit=5)
                err = None
            except Exception as exc:  # noqa: BLE001
                candidates = []
                err = f"{type(exc).__name__}: {exc}"
            ms = (time.perf_counter() - t0) * 1000
            titles = [(c.title or "")[:120] for c in candidates]
            store_row["legacy"].append(
                {
                    "query": query,
                    "ms": round(ms, 1),
                    "count": len(candidates),
                    "target_hit": any(_looks_like_target(t) for t in titles),
                    "titles": titles,
                    "error": err,
                }
            )
        report["stores"][store] = store_row
        print(store, "new_hit", any(q["target_hit"] for q in store_row["queries"]))

    out = (
        ROOT
        / "data"
        / "live-match-reports"
        / f"s25_query_retrieval_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", out)


if __name__ == "__main__":
    main()
