from scout_api.modules.crawler.core.exceptions import ParseError, RequestError
from scout_api.modules.matching.store_search_service import StoreSearchService

svc = StoreSearchService()
try:
    hits = svc.search("shopee", "apple iphone 16 128gb preto", limit=5)
    print("OK", len(hits))
    for h in hits[:5]:
        print("-", h.product_id, (h.title or "")[:80])
except RequestError as e:
    print("RequestError", e.code, str(e)[:200])
except ParseError as e:
    print("ParseError", str(e)[:200])
except Exception as e:
    print(type(e).__name__, str(e)[:200])
