"""Container-friendly live full-store match + diagnostics (no DB/persist)."""

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
from scout_api.modules.crawler.services.html_fetcher import (
    is_auth_wall_page,
    is_challenge_page,
)
from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
    get_shared_html_fetcher,
)
from scout_api.modules.crawler.services.store_resolver import (
    stores_supporting_search,
)
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
from scout_api.modules.matching.store_search_order import order_stores_for_match
from scout_api.modules.matching.store_search_service import StoreSearchService

REF_URL = (
    "https://www.magazineluiza.com.br/"
    "apple-iphone-17-256gb-preto-63-48mp-ios-5g/p/241268000/te/ip17/"
    "?seller_id=magazineluiza"
)

OUT_DIR = Path("/tmp/live-match")
OUT_DIR.mkdir(parents=True, exist_ok=True)

MARKERS = (
    "Algo deu errado",
    "Sorry, we just need to make sure you're not a robot",
    "validateCaptcha",
    "Robot Check",
    "cf-challenge",
    "Just a moment",
    "Access Denied",
    "unusual traffic",
    "captcha",
    "challenge-platform",
    "ap/signin",
    "px-captcha",
    "PerimeterX",
    "datadome",
    "akamai",
    "__NEXT_DATA__",
    "nenhum resultado",
    "não encontramos",
)


def _dump(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return str(obj)


def diagnose_page(html: str, *, url: str, title: str) -> dict[str, Any]:
    low = html.lower()
    return {
        "url": url,
        "title": title[:200],
        "html_len": len(html),
        "challenge": is_challenge_page(html, title=title),
        "auth_wall": is_auth_wall_page(html, url=url, title=title),
        "markers": [m for m in MARKERS if m.lower() in low],
        "has_next_data": "__NEXT_DATA__" in html,
    }


def diagnose_search(store: str, query: str, fetcher: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"store": store, "query": query}
    try:
        from scout_api.modules.matching.search_adapters.registry import (
            resolve_search_adapter,
        )

        adapter = resolve_search_adapter(store)
        search_url = adapter.build_search_request(query).url
        fetch_url = search_url
        out["search_url"] = search_url
        t0 = time.perf_counter()
        response = fetcher.fetch(fetch_url)
        out["fetch_seconds"] = round(time.perf_counter() - t0, 2)
        html = response.text or ""
        title = (response.css("title::text").get() or "").strip()
        out["final_url"] = getattr(response, "url", None)
        out["status"] = getattr(response, "status", None)
        out["page"] = diagnose_page(
            html, url=str(out["final_url"] or fetch_url), title=title
        )
        try:
            cands = adapter.parse_candidates(response)
            out["parsed_candidates"] = len(cands)
            out["candidate_titles"] = [(c.title or "")[:120] for c in cands[:5]]
            out["candidate_urls"] = [c.url for c in cands[:5]]
        except Exception as exc:
            out["parse_error"] = f"{type(exc).__name__}: {exc}"
    except (RequestError, ParseError) as exc:
        out["fetch_error"] = {
            "type": type(exc).__name__,
            "code": getattr(exc, "code", None),
            "message": str(exc)[:500],
        }
    except Exception as exc:
        out["unexpected"] = f"{type(exc).__name__}: {exc}"
        out["traceback"] = traceback.format_exc()[-1500:]
    return out


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
    # Minimal MatchHit-like object for resolve_trusted_gtin
    from scout_api.modules.matching.schemas import MatchHit, MatchReason

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
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    print("=== FULL LIVE MATCH (container) ===", flush=True)
    print("reference=", REF_URL, flush=True)

    scrape = ProductScrapeService()
    search = StoreSearchService()
    engine = MatchingEngine()
    fetcher = get_shared_html_fetcher()

    available = list(stores_supporting_search())
    print("stores=", available, flush=True)

    t0 = time.perf_counter()
    reference = scrape.scrape(REF_URL, include_images=False)
    ref_identity = identity_from_price_item(reference)
    queries = build_search_queries(ref_identity)
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
        "variants=",
        ref_identity.variant_attrs,
        flush=True,
    )
    print("order=", ordered, flush=True)
    print("queries=", queries, flush=True)

    learned: TrustedGtin | None = None
    if ref_identity.gtin:
        learned = TrustedGtin(gtin=ref_identity.gtin, source="reference")

    matches: list[dict[str, Any]] = []
    unmatched: list[str] = []
    errors: list[dict[str, Any]] = []
    best_by_store: dict[str, dict[str, Any]] = {}
    diagnostics: dict[str, Any] = {}

    for store_key in ordered:
        print(f"\n===== STORE {store_key} =====", flush=True)
        if not search.is_search_supported(store_key):
            errors.append(
                {
                    "store": store_key,
                    "code": "SEARCH_UNSUPPORTED",
                    "message": "search unsupported",
                }
            )
            unmatched.append(store_key)
            continue

        store_matched = False
        last_error: dict[str, Any] | None = None
        for query in queries:
            print(f"  query={query!r}", flush=True)
            try:
                candidates = search.search(store_key, query, limit=3)
            except RequestError as exc:
                last_error = {
                    "store": store_key,
                    "code": exc.code,
                    "message": str(exc)[:400],
                }
                print("  SEARCH_ERR", exc.code, str(exc)[:200], flush=True)
                if exc.code == "SEARCH_UNSUPPORTED":
                    break
                continue
            except ParseError as exc:
                last_error = {
                    "store": store_key,
                    "code": "PARSE_ERROR",
                    "message": str(exc)[:400],
                }
                print("  PARSE_ERR", str(exc)[:200], flush=True)
                continue

            print(f"  candidates={len(candidates)}", flush=True)
            for candidate in candidates:
                if canonicalize_url(candidate.url) == canonicalize_url(
                    reference.canonical_url
                ):
                    print("  skip self", candidate.url[:80], flush=True)
                    continue
                try:
                    product = scrape.scrape(candidate.url, include_images=False)
                except (RequestError, ParseError) as exc:
                    print(
                        "  scrape_fail",
                        type(exc).__name__,
                        getattr(exc, "code", None),
                        str(exc)[:180],
                        flush=True,
                    )
                    continue

                cand_id = identity_from_price_item(product)
                score = engine.score(ref_identity, cand_id)
                print(
                    f"  score {score.decision} conf={score.confidence} "
                    f"title={(product.title or '')[:70]} "
                    f"vars={cand_id.variant_attrs} gtin={cand_id.gtin}",
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
                    "country": product.country,
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
                        "variant": product.variant,
                        "gtin": product.gtin,
                        "price": str(product.price)
                        if product.price is not None
                        else None,
                        "currency": product.currency,
                        "availability": product.availability,
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
                            "new_queries=",
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
            # Deep SERP diagnostics for failures
            q = queries[0] if queries else "iphone 17 256gb preto"
            print(f"  diagnosing SERP for {store_key} ...", flush=True)
            diagnostics[store_key] = diagnose_search(store_key, q, fetcher)

    elapsed = time.perf_counter() - t0
    _trusted = resolve_trusted_gtin(
        ref_identity,
        # rebuild minimal hits for consensus
        [],
    )
    # Use learned directly; consensus already applied mid-flight
    report = {
        "started_at": stamp,
        "elapsed_seconds": round(elapsed, 2),
        "reference": {
            "store": reference.store,
            "product_id": reference.product_id,
            "title": reference.title,
            "brand": reference.brand,
            "model": reference.model,
            "variant": reference.variant,
            "gtin": reference.gtin,
            "price": str(reference.price) if reference.price is not None else None,
            "currency": reference.currency,
            "url": reference.url,
            "variant_attrs": ref_identity.variant_attrs,
        },
        "search_order": ordered,
        "queries": queries,
        "discovered_gtin": learned.gtin if learned else None,
        "gtin_source": learned.source if learned else None,
        "matches": matches,
        "unmatched_stores": unmatched,
        "errors": errors,
        "diagnostics": diagnostics,
    }
    path = OUT_DIR / f"full_match_{stamp}.json"
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=_dump),
        encoding="utf-8",
    )
    print("\nREPORT=", path, flush=True)
    print("DONE elapsed=", round(elapsed, 1), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
