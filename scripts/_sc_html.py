from scout_api.modules.crawler.services.product_scrape_service import (
    get_shared_html_fetcher,
)

get_shared_html_fetcher.cache_clear()
url = "https://www.shoppingchina.com.py/produto/celular-apple-iphone-16-a3287-128gb-black-sim-948623"
fetcher = get_shared_html_fetcher()
resp = fetcher.fetch(url)
print("final_url", resp.url)
print("status", getattr(resp, "status", None))
print("len", len(resp.text or ""))
text = resp.text or ""
print("has_price", "price" in text.casefold()[:5000])
print("snippet", text[:500].replace("\n", " ")[:400])
# try alternate producto URL
url2 = url.replace("/produto/", "/producto/")
resp2 = fetcher.fetch(url2)
print("alt", resp2.url, len(resp2.text or ""))
print("alt_snip", (resp2.text or "")[:300].replace("\n", " "))
