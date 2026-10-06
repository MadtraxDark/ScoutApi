from scout_api.modules.crawler.services.product_scrape_service import (
    get_shared_html_fetcher,
)
from scout_api.modules.matching.search_adapters.paraguay.shoppingchina import (
    ShoppingChinaSearchAdapter,
)

get_shared_html_fetcher.cache_clear()
adapter = ShoppingChinaSearchAdapter()
q = "playstation 5 digital"
url = adapter.build_search_request(q).url
print("URL", url, flush=True)
resp = get_shared_html_fetcher().fetch(url)
print(
    "status",
    getattr(resp, "status_code", None),
    "final",
    getattr(resp, "url", None),
    "len",
    len(resp.text or ""),
    flush=True,
)
print("snippet", (resp.text or "")[:500].replace("\n", " "), flush=True)
cands = adapter.parse_candidates(resp)
print("n", len(cands), flush=True)
for c in cands[:5]:
    print("-", c.title, c.url, flush=True)
