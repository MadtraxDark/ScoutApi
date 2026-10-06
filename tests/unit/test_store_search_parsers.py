"""Unit tests for store SERP parsers (HTML fixtures, no network)."""

from __future__ import annotations

from scrapy.http import HtmlResponse

from scout_api.modules.matching.search_adapters.brazil.amazon import (
    AmazonBrazilSearchAdapter,
)
from scout_api.modules.matching.search_adapters.brazil.kabum import KabumSearchAdapter
from scout_api.modules.matching.search_adapters.brazil.magazineluiza import (
    MagazineLuizaSearchAdapter,
)
from scout_api.modules.matching.search_adapters.brazil.shopee import ShopeeSearchAdapter
from scout_api.modules.matching.search_adapters.paraguay.nissei import (
    NisseiSearchAdapter,
)
from scout_api.modules.matching.search_adapters.paraguay.shoppingchina import (
    ShoppingChinaSearchAdapter,
)
from scout_api.modules.matching.search_adapters.registry import (
    registered_search_store_keys,
)
from scout_api.modules.matching.search_adapters.usa.amazon import AmazonUSSearchAdapter
from scout_api.modules.matching.search_adapters.usa.bestbuy import BestBuySearchAdapter


def _response(url: str, body: str) -> HtmlResponse:
    return HtmlResponse(url=url, body=body.encode("utf-8"), encoding="utf-8")


def test_kabum_parse_search_results() -> None:
    html = """
    <html><body>
      <a class="productLink" href="/produto/123456/memoria-kingston">Kingston</a>
      <a href="/produto/123456/memoria-kingston">dup</a>
      <a href="/produto/999/outro">Outro</a>
    </body></html>
    """
    adapter = KabumSearchAdapter()
    results = adapter.parse_candidates(
        _response("https://www.kabum.com.br/busca/kingston", html)
    )
    assert len(results) == 2
    assert results[0].product_id == "123456"
    assert "kabum.com.br/produto/" in results[0].url


def test_kabum_parse_search_next_data_catalog() -> None:
    """Modern KaBuM SERP embeds catalog cards in __NEXT_DATA__, not anchors."""
    import json

    payload = {
        "props": {
            "pageProps": {
                "data": {
                    "catalogServer": {
                        "data": [
                            {
                                "code": 777166,
                                "name": (
                                    "Placa de Vídeo MSI RTX 5070 12G Shadow 3X OC "
                                    "NVIDIA GeForce 12GB GDDR7"
                                ),
                                "friendlyName": (
                                    "placa-de-video-msi-rtx-5070-12g-shadow-3x-oc-"
                                    "nvidia-geforce-12gb-gddr7"
                                ),
                            },
                            {
                                "code": 111,
                                "name": "Outro produto",
                                "friendlyName": "outro-produto",
                            },
                        ]
                    }
                }
            }
        }
    }
    html = f"""
    <html><body>
      <script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>
    </body></html>
    """
    adapter = KabumSearchAdapter()
    results = adapter.parse_candidates(
        _response("https://www.kabum.com.br/busca/msi%20rtx%205070", html)
    )
    assert len(results) == 2
    assert results[0].product_id == "777166"
    assert "777166" in results[0].url
    assert results[0].title and "Shadow 3X" in results[0].title
    assert results[0].metadata.get("source") == "kabum-search-next-data"


def test_magalu_parse_search_results() -> None:
    html = """
    <html><body>
      <a data-testid="product-card-link" href="/iphone/p/jjhd6g4f9d/">iPhone</a>
      <a href="/busca/iphone/">ignore</a>
    </body></html>
    """
    adapter = MagazineLuizaSearchAdapter()
    results = adapter.parse_candidates(
        _response("https://www.magazineluiza.com.br/busca/iphone/", html)
    )
    assert len(results) == 1
    assert results[0].product_id == "jjhd6g4f9d"


def test_amazon_br_parse_search_results() -> None:
    html = """
    <html><body>
      <div data-component-type="s-search-result" data-asin="B0TESTASIN">
        <h2><a href="/dp/B0TESTASIN"><span>Produto Amazon</span></a></h2>
      </div>
      <div data-component-type="s-search-result" data-asin="">
        <h2><a href="/s?k=x"><span>Sponsored</span></a></h2>
      </div>
    </body></html>
    """
    adapter = AmazonBrazilSearchAdapter()
    results = adapter.parse_candidates(
        _response("https://www.amazon.com.br/s?k=teste", html)
    )
    assert len(results) == 1
    assert results[0].product_id == "B0TESTASIN"
    assert results[0].title == "Produto Amazon"


def test_amazon_us_parse_search_results() -> None:
    html = """
    <html><body>
      <div data-component-type="s-search-result" data-asin="B0USASIN01">
        <h2><a href="/dp/B0USASIN01"><span>US Product</span></a></h2>
      </div>
    </body></html>
    """
    adapter = AmazonUSSearchAdapter()
    results = adapter.parse_candidates(
        _response("https://www.amazon.com/s?k=teste", html)
    )
    assert len(results) == 1
    assert results[0].product_id == "B0USASIN01"
    assert results[0].metadata.get("source") == "amazon-us-search"


def test_bestbuy_parse_search_results() -> None:
    html = """
    <html><body>
      <a href="/product/sony-headphones/JJ123456">Sony</a>
      <a href="/site/sony-wh/6556754.p?skuId=6556754">Legacy</a>
      <a href="/site/searchpage.jsp?st=sony">ignore</a>
    </body></html>
    """
    adapter = BestBuySearchAdapter()
    results = adapter.parse_candidates(
        _response("https://www.bestbuy.com/site/searchpage.jsp?st=sony", html)
    )
    assert len(results) == 2
    assert results[0].product_id == "JJ123456"
    assert results[1].product_id == "6556754"


def test_nissei_parse_search_results() -> None:
    html = """
    <html><body>
      <a class="product-item-link" href="/py/apple-iphone-17-a3258-dual">iPhone</a>
      <a href="/py/catalogsearch/result/?q=iphone">ignore</a>
    </body></html>
    """
    adapter = NisseiSearchAdapter()
    results = adapter.parse_candidates(
        _response("https://nissei.com/py/catalogsearch/result/?q=iphone", html)
    )
    assert len(results) == 1
    assert results[0].url.endswith("/py/apple-iphone-17-a3258-dual")


def test_nissei_search_prefers_browser_for_cloudflare() -> None:
    req = NisseiSearchAdapter().build_search_request("rtx 4060")
    assert req.prefer_browser is True
    assert "nissei.com/br/catalogsearch/result" in req.url


def test_nissei_classify_empty_incomplete_home_shell() -> None:
    adapter = NisseiSearchAdapter()
    # Locale-less catalogsearch redirects to home — must not look like NO_MATCH.
    home = _response(
        "https://nissei.com/",
        "<html><body><h1>Nissei</h1></body></html>",
    )
    assert adapter.classify_empty_result(home) == "incomplete"
    cf_shell = _response(
        "https://nissei.com/br/catalogsearch/result/?q=x",
        "<html><head><title>Just a moment...</title></head>"
        "<body>cf-challenge</body></html>",
    )
    assert adapter.classify_empty_result(cf_shell) == "incomplete"
    genuine = _response(
        "https://nissei.com/br/catalogsearch/result/?q=zzzz",
        "<html><body><div class='message notice'>"
        "Your search returned no results.</div></body></html>",
    )
    assert adapter.classify_empty_result(genuine) == "genuine_empty"


def test_shoppingchina_parse_search_results() -> None:
    html = """
    <html><body>
      <a href="/producto/notebook-asus-12345/">Notebook</a>
      <a href="/produto/fone-999/">Fone</a>
      <a href="/site/search?query=x">ignore</a>
    </body></html>
    """
    adapter = ShoppingChinaSearchAdapter()
    results = adapter.parse_candidates(
        _response(
            "https://www.shoppingchina.com.py/site/search?query=note",
            html,
        )
    )
    assert len(results) == 2
    assert results[0].product_id == "12345"
    assert results[1].product_id == "999"


def test_shoppingchina_parse_quick_search_json() -> None:
    payload = """
    [
      {
        "title_po": "iPhone 17 256GB Preto",
        "url_po": "https://www.shoppingchina.com.py/produto/celular-apple-iphone-17-256gb-preto-1012345",
        "url_es": "https://www.shoppingchina.com.py/producto/celular-apple-iphone-17-256gb-preto-1012345"
      }
    ]
    """
    adapter = ShoppingChinaSearchAdapter()
    results = adapter.parse_candidates(
        _response(
            "https://www.shoppingchina.com.py/quick_search?search=iphone",
            payload,
        )
    )
    assert len(results) == 1
    assert results[0].product_id == "1012345"
    assert results[0].title == "iPhone 17 256GB Preto"
    assert results[0].metadata.get("source") == "shoppingchina-quick-search"


def test_shopee_parse_search_results() -> None:
    html = """
    <html><body>
      <a data-sqe="link" href="/Memoria-Kingston-i.341936748.29277977480">RAM</a>
      <a href="/search?keyword=x">ignore</a>
    </body></html>
    """
    adapter = ShopeeSearchAdapter()
    results = adapter.parse_candidates(
        _response("https://shopee.com.br/search?keyword=kingston", html)
    )
    assert len(results) == 1
    assert results[0].product_id == "29277977480"
    assert results[0].metadata.get("shop_id") == "341936748"


def test_shopee_parse_search_api_payload() -> None:
    html = """
    <html><body>
      <script type="application/json" data-shopee-search="1">
        {"items":[{"item_basic":{"itemid":999,"shopid":111,"name":"iPhone 17"}}]}
      </script>
    </body></html>
    """
    adapter = ShopeeSearchAdapter()
    results = adapter.parse_candidates(
        _response("https://shopee.com.br/search?keyword=iphone", html)
    )
    assert len(results) == 1
    assert results[0].product_id == "999"
    assert "product/111/999" in results[0].url
    assert results[0].metadata.get("source") == "shopee-search-api"


def test_shopee_parse_search_embedded_fallback() -> None:
    html = '<html><body><script>const x="i.111.222";</script></body></html>'
    adapter = ShopeeSearchAdapter()
    results = adapter.parse_candidates(
        _response("https://shopee.com.br/search?keyword=x", html)
    )
    assert len(results) == 1
    assert results[0].product_id == "222"
    assert "product/111/222" in results[0].url


def test_build_search_urls() -> None:
    assert "busca/" in KabumSearchAdapter().build_search_request("rtx 4060").url
    assert "busca/" in MagazineLuizaSearchAdapter().build_search_request("rtx 4060").url
    assert "s?k=" in AmazonBrazilSearchAdapter().build_search_request("rtx 4060").url
    assert (
        "amazon.com/s?k="
        in AmazonUSSearchAdapter().build_search_request("rtx 4060").url
    )
    assert (
        "searchpage.jsp" in BestBuySearchAdapter().build_search_request("rtx 4060").url
    )
    assert (
        "nissei.com/br/catalogsearch/result"
        in NisseiSearchAdapter().build_search_request("rtx 4060").url
    )
    assert (
        "quick_search?search="
        in ShoppingChinaSearchAdapter().build_search_request("rtx 4060").url
    )
    assert (
        "search?keyword=" in ShopeeSearchAdapter().build_search_request("rtx 4060").url
    )


def test_all_implemented_stores_support_search() -> None:
    supported = set(registered_search_store_keys())
    expected = {
        "amazon_br",
        "amazon_us",
        "kabum",
        "magazineluiza",
        "shopee",
        "bestbuy",
        "nissei",
        "shoppingchina",
    }
    assert expected <= supported
