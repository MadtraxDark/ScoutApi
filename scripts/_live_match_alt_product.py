"""Live full match with an alternate reference product (Kabum notebook)."""

from __future__ import annotations

import json
import sys
import time
import traceback
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, "/app/src")

from scout_api.modules.crawler.core.exceptions import ParseError, RequestError
from scout_api.modules.crawler.core.fingerprints import canonicalize_url
from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
)
from scout_api.modules.crawler.services.store_resolver import stores_supporting_search
from scout_api.modules.matching.engine import MatchingEngine
from scout_api.modules.matching.gtin_learning import (
    TrustedGtin,
    identity_with_gtin,
    resolve_trusted_gtin,
)
from scout_api.modules.matching.identity import (
    build_search_queries,
    identity_from_price_item,
    normalize_gtin,
)
from scout_api.modules.matching.schemas import MatchHit, MatchReason
from scout_api.modules.matching.store_search_order import order_stores_for_match
from scout_api.modules.matching.store_search_service import StoreSearchService

# Different from Magalu iPhone 17 — Lenovo IdeaPad on Kabum (often has EAN).
REF_URL = (
    "https://www.kabum.com.br/produto/999541/"
    "notebook-lenovo-ideapad-slim-3i-intel-core-3-100u-8gb-256gb-ssd-"
    "windows-11-15-3-83nu0000br-luna-grey"
)

OUT = Path("/home/app/live_match_alt_report.json")
SLEEP = 16
MAX_CANDIDATES = 3


def maybe_learn(
    current: TrustedGtin | None,
    ref_identity: Any,
    *,
    store: str,
    decision: str,
    product: Any,
) -> TrustedGtin | None:
    cand = normalize_gtin(getattr(product, "gtin", None))
    if not cand or decision != "auto_match":
        return current
    hit = MatchHit(
        store=store,
        country=getattr(product, "country", "") or "",
        decision=decision,  # type: ignore[arg-type]
        confidence=Decimal("0.99"),
        reasons=[MatchReason(code="probe", detail="x", score=1.0)],
        product=product,
    )
    probe = resolve_trusted_gtin(ref_identity, [hit])
    if probe is None:
        return current
    if current is None:
        return TrustedGtin(gtin=cand, source=f"auto_match:{store}")
    if current.gtin == cand:
        return current
    return current if current.source == "reference" else None


def main() -> int:
    scrape = ProductScrapeService()
    search = StoreSearchService()
    engine = MatchingEngine()

    print("REF", REF_URL, flush=True)
    reference = scrape.scrape(REF_URL, include_images=False)
    ref_identity = identity_from_price_item(reference)
    queries = build_search_queries(ref_identity)
    available = list(stores_supporting_search())
    ordered = order_stores_for_match(
        available,
        reference_store=reference.store,
        reference_has_gtin=bool(ref_identity.gtin),
    )
    print(
        "reference_ok",
        reference.title,
        reference.price,
        "gtin=",
        ref_identity.gtin,
        "brand=",
        ref_identity.brand,
        "model=",
        ref_identity.model,
        "vars=",
        ref_identity.variant_attrs,
        flush=True,
    )
    print("order=", ordered, flush=True)
    print("queries=", queries, flush=True)
    time.sleep(SLEEP)

    learned: TrustedGtin | None = None
    if ref_identity.gtin:
        learned = TrustedGtin(gtin=ref_identity.gtin, source="reference")

    matches: list[dict[str, Any]] = []
    unmatched: list[str] = []
    errors: list[dict[str, Any]] = []
    best_by_store: dict[str, dict[str, Any]] = {}

    for store_key in ordered:
        print(f"\n===== STORE {store_key} =====", flush=True)
        if not search.is_search_supported(store_key):
            unmatched.append(store_key)
            errors.append(
                {"store": store_key, "code": "SEARCH_UNSUPPORTED", "message": "n/a"}
            )
            continue

        store_matched = False
        last_error: dict[str, Any] | None = None
        for query in queries:
            print(f"  query={query!r}", flush=True)
            time.sleep(SLEEP)
            try:
                candidates = search.search(store_key, query, limit=MAX_CANDIDATES)
            except RequestError as exc:
                last_error = {
                    "store": store_key,
                    "code": exc.code,
                    "message": str(exc)[:400],
                }
                print("  SEARCH_ERR", exc.code, str(exc)[:180], flush=True)
                if exc.code == "SEARCH_UNSUPPORTED":
                    break
                continue
            except ParseError as exc:
                last_error = {
                    "store": store_key,
                    "code": "PARSE_ERROR",
                    "message": str(exc)[:400],
                }
                print("  PARSE_ERR", str(exc)[:180], flush=True)
                continue

            print(f"  candidates={len(candidates)}", flush=True)
            for candidate in candidates:
                if canonicalize_url(candidate.url) == canonicalize_url(
                    reference.canonical_url
                ):
                    print("  skip self", flush=True)
                    continue
                time.sleep(SLEEP)
                try:
                    product = scrape.scrape(candidate.url, include_images=False)
                except (RequestError, ParseError) as exc:
                    print(
                        "  scrape_fail",
                        getattr(exc, "code", type(exc).__name__),
                        str(exc)[:160],
                        flush=True,
                    )
                    continue

                cand_id = identity_from_price_item(product)
                score = engine.score(ref_identity, cand_id)
                print(
                    f"  score {score.decision} conf={score.confidence} "
                    f"title={(product.title or '')[:70]} "
                    f"vars={cand_id.variant_attrs} gtin={cand_id.gtin} "
                    f"model={cand_id.model}",
                    flush=True,
                )
                print(
                    "    reasons=",
                    [(r.code, r.detail) for r in score.reasons],
                    flush=True,
                )
                if score.decision == "reject":
                    continue

                hit = {
                    "store": product.store,
                    "decision": score.decision,
                    "confidence": str(score.confidence),
                    "reasons": [
                        {"code": r.code, "detail": r.detail, "score": r.score}
                        for r in score.reasons
                    ],
                    "search_query": query,
                    "product": {
                        "product_id": product.product_id,
                        "title": product.title,
                        "brand": product.brand,
                        "model": product.model,
                        "gtin": product.gtin,
                        "price": str(product.price)
                        if product.price is not None
                        else None,
                        "currency": product.currency,
                        "url": product.url,
                        "variant_attrs": cand_id.variant_attrs,
                    },
                }
                existing = best_by_store.get(store_key)
                if existing is None or Decimal(hit["confidence"]) > Decimal(
                    existing["confidence"]
                ):
                    best_by_store[store_key] = hit
                if score.decision == "auto_match":
                    store_matched = True
                    learned = maybe_learn(
                        learned,
                        ref_identity,
                        store=product.store,
                        decision=score.decision,
                        product=product,
                    )
                    if learned and not ref_identity.gtin:
                        ref_identity = identity_with_gtin(ref_identity, learned.gtin)
                        queries = build_search_queries(ref_identity)
                        print(
                            "  LEARNED_GTIN",
                            learned.gtin,
                            learned.source,
                            "queries=",
                            queries,
                            flush=True,
                        )
            if store_matched:
                break

        if store_key in best_by_store:
            matches.append(best_by_store[store_key])
        else:
            unmatched.append(store_key)
            if last_error is not None:
                errors.append(last_error)

    report = {
        "started_at": datetime.now(UTC).isoformat(),
        "reference_url": REF_URL,
        "reference": {
            "store": reference.store,
            "product_id": reference.product_id,
            "title": reference.title,
            "brand": reference.brand,
            "model": reference.model,
            "gtin": reference.gtin,
            "price": str(reference.price) if reference.price is not None else None,
            "currency": reference.currency,
            "variant_attrs": ref_identity.variant_attrs,
        },
        "search_order": ordered,
        "queries": queries,
        "discovered_gtin": learned.gtin if learned else None,
        "gtin_source": learned.source if learned else None,
        "matches": matches,
        "unmatched_stores": unmatched,
        "errors": errors,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nREPORT", OUT, flush=True)
    print(
        "SUMMARY matches=",
        [(m["store"], m["decision"], m["confidence"]) for m in matches],
        "unmatched=",
        unmatched,
        flush=True,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        traceback.print_exc()
        raise
