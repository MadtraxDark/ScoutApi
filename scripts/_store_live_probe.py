import json
import time

from scout_api.modules.crawler.core.exceptions import ParseError, RequestError
from scout_api.modules.crawler.core.scrape_guard import ScrapeGuard
from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
    get_shared_html_fetcher,
)
from scout_api.modules.matching.store_search_service import StoreSearchService

get_shared_html_fetcher.cache_clear()
guard = ScrapeGuard(
    url_cooldown_seconds=0, domain_min_interval_seconds=1, result_cache_ttl_seconds=0
)
scrape = ProductScrapeService(guard=guard)
search = StoreSearchService()

# Known live PDPs (validated in recent sessions) + one Magalu reference.
PDPS = {
    ("magazineluiza"): (
        "https://www.magazineluiza.com.br/apple-iphone"
        "-16-128gb-preto-61-48mp-ios-5g/p/238803400/te"
        "/ip16/?seller_id=magazineluiza"
    ),
    ("kabum"): (
        "https://www.kabum.com.br/produto/640052/iphon"
        "e-16-apple-128gb-preto-tela-de-6-1-camera-dup"
        "la-de-48mp-ios-5g"
    ),
    ("shoppingchina"): (
        "https://www.shoppingchina.com.py/producto/cel"
        "ular-apple-iphone-16-a3287-128gb-black-sim-94"
        "8623"
    ),
    "nissei": "https://nissei.com/py/apple-iphone-16-a3287-128-gb-black",
    ("bestbuy"): (
        "https://www.bestbuy.com/product/apple-iphone-"
        "16-128gb-apple-intelligence-black-verizon/JCQ"
        "6HRGR8C"
    ),
    ("amazon_br"): (
        "https://www.amazon.com.br/dp/B0DGHMQ2F4"
    ),  # may vary; search fallback below
    "amazon_us": "https://www.amazon.com/dp/B0DGHMQ2F4",
}

SEARCH_STORES = [
    "magazineluiza",
    "kabum",
    "shoppingchina",
    "nissei",
    "bestbuy",
    "amazon_br",
    "amazon_us",
    "shopee",
]
QUERY = "apple iphone 16 128gb preto"

results = {"scrape": {}, "search": {}}

for store, url in PDPS.items():
    print(f"SCRAPE {store}", flush=True)
    t0 = time.time()
    try:
        item = scrape.scrape(url, include_images=False)
        results["scrape"][store] = {
            "ok": True,
            "secs": round(time.time() - t0, 1),
            "title": (item.title or "")[:90],
            "price": str(item.price) if item.price is not None else None,
            "currency": item.currency,
            "available": item.available,
            "product_id": item.product_id,
            "url": item.url,
        }
    except RequestError as e:
        results["scrape"][store] = {
            "ok": False,
            "secs": round(time.time() - t0, 1),
            "code": e.code,
            "msg": str(e)[:180],
        }
    except ParseError as e:
        results["scrape"][store] = {
            "ok": False,
            "secs": round(time.time() - t0, 1),
            "code": "PARSE_ERROR",
            "msg": str(e)[:180],
        }
    except Exception as e:
        results["scrape"][store] = {
            "ok": False,
            "secs": round(time.time() - t0, 1),
            "code": type(e).__name__,
            "msg": str(e)[:180],
        }
    print(json.dumps(results["scrape"][store], ensure_ascii=False), flush=True)

for store in SEARCH_STORES:
    q = (
        "apple iphone 16 128gb black"
        if store in {"bestbuy", "amazon_us", "shoppingchina", "nissei"}
        else QUERY
    )
    print(f"SEARCH {store} q={q}", flush=True)
    t0 = time.time()
    try:
        hits = search.search(store, q, limit=3)
        results["search"][store] = {
            "ok": True,
            "secs": round(time.time() - t0, 1),
            "count": len(hits),
            "sample": [
                {"title": (h.title or "")[:70], "url": h.url, "id": h.product_id}
                for h in hits[:3]
            ],
        }
    except RequestError as e:
        results["search"][store] = {
            "ok": False,
            "secs": round(time.time() - t0, 1),
            "code": e.code,
            "msg": str(e)[:180],
        }
    except Exception as e:
        results["search"][store] = {
            "ok": False,
            "secs": round(time.time() - t0, 1),
            "code": type(e).__name__,
            "msg": str(e)[:180],
        }
    print(json.dumps(results["search"][store], ensure_ascii=False), flush=True)

print("SUMMARY", flush=True)
print(json.dumps(results, ensure_ascii=False, indent=2), flush=True)
