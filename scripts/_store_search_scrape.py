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

# Already OK from previous run — reconfirm search for all match stores + scrape first
# hit
STORES = {
    "magazineluiza": "apple iphone 16 128gb preto",
    "kabum": "apple iphone 16 128gb preto",
    "shoppingchina": "apple iphone 16 128gb black",
    "nissei": "apple iphone 16 128gb black",
    "bestbuy": "apple iphone 16 128gb black",
    "amazon_br": "apple iphone 16 128gb preto",
    "amazon_us": "apple iphone 16 128gb black",
    "shopee": "apple iphone 16 128gb preto",
}
out = {}
for store, q in STORES.items():
    print("STORE", store, flush=True)
    row = {"query": q}
    t0 = time.time()
    try:
        hits = search.search(store, q, limit=3)
        row["search"] = {
            "ok": True,
            "secs": round(time.time() - t0, 1),
            "count": len(hits),
            "sample": [
                {"title": (h.title or "")[:75], "url": h.url, "id": h.product_id}
                for h in hits[:3]
            ],
        }
    except RequestError as e:
        row["search"] = {
            "ok": False,
            "secs": round(time.time() - t0, 1),
            "code": e.code,
            "msg": str(e)[:160],
        }
        hits = []
    except Exception as e:
        row["search"] = {
            "ok": False,
            "secs": round(time.time() - t0, 1),
            "code": type(e).__name__,
            "msg": str(e)[:160],
        }
        hits = []
    print("  search", json.dumps(row["search"], ensure_ascii=False), flush=True)

    # scrape first hit if any; else skip
    if hits:
        url = hits[0].url
        t0 = time.time()
        try:
            item = scrape.scrape(url, include_images=False)
            row["scrape"] = {
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
            row["scrape"] = {
                "ok": False,
                "secs": round(time.time() - t0, 1),
                "code": e.code,
                "msg": str(e)[:160],
                "url": url,
            }
        except ParseError as e:
            row["scrape"] = {
                "ok": False,
                "secs": round(time.time() - t0, 1),
                "code": "PARSE_ERROR",
                "msg": str(e)[:160],
                "url": url,
            }
        except Exception as e:
            row["scrape"] = {
                "ok": False,
                "secs": round(time.time() - t0, 1),
                "code": type(e).__name__,
                "msg": str(e)[:160],
                "url": url,
            }
        print("  scrape", json.dumps(row["scrape"], ensure_ascii=False), flush=True)
    else:
        row["scrape"] = {"ok": False, "skipped": True, "reason": "no_search_hits"}
        print("  scrape skipped", flush=True)
    out[store] = row

print("FINAL", flush=True)
print(json.dumps(out, ensure_ascii=False, indent=2), flush=True)
