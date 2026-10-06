from scout_api.modules.crawler.services.amazon_http_first_fetcher import (
    has_buybox_price_signal,
    looks_like_amazon_pdp,
    looks_like_amazon_search,
)
from scout_api.modules.crawler.services.product_scrape_service import (
    get_shared_html_fetcher,
)

resp = get_shared_html_fetcher().fetch("https://www.amazon.com.br/dp/B0HJBCQ9B7")
print("search", looks_like_amazon_search(resp))
print("pdp", looks_like_amazon_pdp(resp))
print("buybox", has_buybox_price_signal(resp))
print(
    "s-search-result",
    bool(resp.css("div[data-component-type='s-search-result']").get()),
)
print("s-result-item", bool(resp.css("div.s-result-item[data-asin]").get()))
print("data-asin count", (resp.text or "").lower().count("data-asin="))
print("bytes", len((resp.text or "").encode("utf-8", errors="replace")))
print("metrics", resp.meta.get("fetch_metrics"))
print("url path has /dp/", "/dp/" in (resp.url or ""))
print("url path has /s", "/s" in (resp.url or "").split("?", 1)[0])
