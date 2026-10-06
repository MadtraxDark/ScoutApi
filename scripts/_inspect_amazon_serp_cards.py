from pathlib import Path

from scrapy.http import HtmlResponse, Request

from scout_api.modules.matching.search_adapters.amazon.parse import (
    parse_amazon_search_results,
)

html = Path("/tmp/amazon_br_serp_html/shared_amazon_http_first.html").read_text(
    encoding="utf-8", errors="replace"
)
url = "https://www.amazon.com.br/s?k=x"
resp = HtmlResponse(
    url, body=html.encode("utf-8"), encoding="utf-8", request=Request(url)
)
cards = resp.css("div[data-component-type='s-search-result']")[:3]
for i, card in enumerate(cards):
    asin = card.attrib.get("data-asin")
    hrefs = card.css("h2 a::attr(href), a.a-link-normal::attr(href)").getall()[:3]
    texts = [
        t.strip()
        for t in card.css(
            "h2 a span::text, h2 span::text, span.a-size-base-plus::text, "
            "span.a-size-medium::text"
        ).getall()
        if t and t.strip()
    ][:8]
    print("---", i, asin)
    print("hrefs", hrefs[:2])
    print("texts", texts[:5])
    print("h2", (card.css("h2").get() or "")[:400])

parsed = parse_amazon_search_results(
    resp, host="amazon.com.br", source="diag", limit=5
)
for c in parsed:
    print("parsed", c.product_id, (c.title or "")[:80], c.metadata.get("source"))
