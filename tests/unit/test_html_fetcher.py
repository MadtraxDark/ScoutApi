from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.error import HTTPError

import pytest
from scrapy.http import HtmlResponse, Request

from scout_api.modules.crawler.core.exceptions import RequestError
from scout_api.modules.crawler.core.scrape_guard import ScrapeGuard
from scout_api.modules.crawler.services.html_fetcher import (
    CamoufoxHtmlFetcher,
    UrllibHtmlFetcher,
    is_akamai_sec_cpt_page,
    is_challenge_page,
    is_hard_block_page,
    proxy_settings_from_url,
)
from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
)


def test_is_challenge_page_detects_cloudflare_and_akamai() -> None:
    assert is_challenge_page("<html></html>", title="Just a moment...")
    assert is_challenge_page("<html></html>", title="Un momento…")
    assert is_challenge_page("<html></html>", title="Loading https://nissei.com/py/x")
    assert is_challenge_page(
        '<link rel="stylesheet" href="https://wx.mlcdn.com.br/akamai-bot/css/x.css">'
        "<h1>Não é possível acessar a página</h1>"
    )
    assert not is_challenge_page(
        "<html><body><h1>Produto</h1><p>R$ 10,00</p></body></html>",
        title="Produto",
    )
    assert is_challenge_page(
        '<html><form action="/errors/validateCaptcha">'
        "<h4>Enter the characters you see below</h4></form></html>",
        title="Amazon.com",
    )
    assert is_challenge_page(
        "<html><button id='continue-button'></button>"
        "<script>verifyChallenge()</script>"
        "<script src='snoopy-generation-web/x.js'></script></html>"
    )
    akamai_sec = (
        "<!DOCTYPE html><html><head></head><body>"
        '<script src="/ua_lYE/DKZaoN/8RdfYr/x?v=9d2316eb-63cf-59fa"></script>'
        '<div id="sec-if-cpt-container" role="main">'
        '<div class="behavioral-content">'
        '<div id="sec-bc-text-container"></div>'
        '<div id="sec-bc-tile-parent"></div>'
        "</div></div></body></html>"
    )
    assert is_challenge_page(akamai_sec)
    assert is_akamai_sec_cpt_page(akamai_sec)
    # Large PDP mentioning akamai must not be treated as sec-cpt interstitial.
    assert not is_akamai_sec_cpt_page(
        "<html><body>" + ("produto " * 20_000) + "sec-if-cpt-container</body></html>"
    )


def test_is_hard_block_page_detects_cloudflare_ip_ban() -> None:
    assert is_hard_block_page(
        "<html><h1>Sorry, you have been blocked</h1></html>",
        title="Attention Required! | Cloudflare",
    )
    assert not is_hard_block_page(
        "<html><body>Performing security verification</body></html>",
        title="Just a moment...",
    )


def test_proxy_settings_from_url_parses_credentials() -> None:
    assert proxy_settings_from_url("http://user:p%40ss@gate.example:8080") == {
        "server": "http://gate.example:8080",
        "username": "user",
        "password": "p@ss",
    }


def test_locale_and_warmup_for_url() -> None:
    from scout_api.modules.crawler.services.html_fetcher import (
        apply_shopee_br_proxy_targeting,
        is_auth_wall_page,
        is_shopee_get_pc_url,
        is_shopee_traffic_block,
        locale_for_url,
        looks_like_shopee_pdp,
        shopee_ids_from_url,
        warmup_url_for,
        wrap_shopee_pdp_json,
    )

    assert locale_for_url("https://nissei.com/py/x") == "es-PY"
    assert locale_for_url("https://www.magazineluiza.com.br/p/1") == "pt-BR"
    assert locale_for_url("https://shopee.com.br/i.1.2") == "pt-BR"
    assert locale_for_url("https://www.amazon.com.br/dp/B09WNK39JN") == "pt-BR"
    assert locale_for_url("https://www.kabum.com.br/produto/1") == "pt-BR"
    assert locale_for_url("https://www.pichau.com.br/produto/1") == "pt-BR"
    assert locale_for_url("https://www.amazon.com/dp/B09WNK39JN") == "en-US"
    assert locale_for_url("https://www.bestbuy.com/product/x/1") == "en-US"
    assert locale_for_url("https://example.com/") is None
    from scout_api.modules.crawler.services.html_fetcher import (
        apply_proxy_geo_targeting,
        marketplace_referer_for_url,
    )

    assert (
        marketplace_referer_for_url("https://www.amazon.com.br/dp/B09WNK39JN")
        == "https://www.amazon.com.br/"
    )
    assert (
        marketplace_referer_for_url("https://www.amazon.com/dp/B09WNK39JN")
        == "https://www.amazon.com/"
    )
    assert marketplace_referer_for_url("https://www.kabum.com.br/p/1") is None
    assert marketplace_referer_for_url(
        "https://www.bestbuy.com/product/ps5/JXHQ37TYYL"
    ) == ("https://www.bestbuy.com/")
    assert warmup_url_for("https://nissei.com/py/produto") == "https://nissei.com/py/"
    assert warmup_url_for("https://www.magazineluiza.com.br/p/1") == (
        "https://www.magazineluiza.com.br/"
    )
    assert (
        warmup_url_for("https://shopee.com.br/item-i.1.2") == "https://shopee.com.br/"
    )
    assert warmup_url_for("https://www.bestbuy.com/product/ps5/1") == (
        "https://www.bestbuy.com/"
    )
    assert shopee_ids_from_url(
        "https://shopee.com.br/prod-i.341936748.29277977480"
    ) == ("341936748", "29277977480")
    assert looks_like_shopee_pdp('{"data":{"item":{"item_id":1}}}') is True
    assert looks_like_shopee_pdp("<html>verify</html>") is False
    assert is_shopee_get_pc_url(
        "https://shopee.com.br/api/v4/pdp/get_pc?shop_id=1&item_id=2"
    )
    assert is_shopee_traffic_block(
        "https://shopee.com.br/verify/traffic/error?next=x", ""
    )
    assert is_auth_wall_page(
        "<html><body>verify/traffic</body></html>",
        url="https://shopee.com.br/verify/traffic?anti_bot_tracking_id=x",
        title="verify",
    )
    assert is_auth_wall_page(
        "<html><body>Verificação de conta</body></html>",
        url=(
            "https://www.mercadolivre.com.br/gz/account-verification"
            "?go=https%3A%2F%2Flista.mercadolivre.com.br%2Frtx"
        ),
        title="Mercado Libre",
    )
    assert not is_auth_wall_page(
        "<html><body class='ui-search-layout'>card</body></html>",
        url="https://lista.mercadolivre.com.br/rtx-5060",
        title="RTX 5060",
    )
    assert "data-shopee-pdp" in wrap_shopee_pdp_json('{"item":{"item_id":1}}')
    assert (
        apply_shopee_br_proxy_targeting(
            "http://login:pass@gw.dataimpulse.com:10001",
            "https://shopee.com.br/i.1.2",
        )
        == "http://login__cr.br:pass@gw.dataimpulse.com:10001"
    )
    assert (
        apply_shopee_br_proxy_targeting(
            "http://login__cr.br:pass@gw.dataimpulse.com:10001",
            "https://shopee.com.br/i.1.2",
        )
        == "http://login__cr.br:pass@gw.dataimpulse.com:10001"
    )
    assert (
        apply_shopee_br_proxy_targeting(
            "http://login:pass@gw.dataimpulse.com:10001",
            "https://www.kabum.com.br/p/1",
        )
        == "http://login:pass@gw.dataimpulse.com:10001"
    )
    assert (
        apply_proxy_geo_targeting(
            "http://login:pass@gw.dataimpulse.com:10001",
            "https://www.bestbuy.com/product/ps5/JXHQ37TYYL",
        )
        == "http://login__cr.us%3Bsessid.scoutbb:pass@gw.dataimpulse.com:10001"
    )
    assert (
        apply_proxy_geo_targeting(
            "http://login__cr.br:pass@gw.dataimpulse.com:10001",
            "https://www.bestbuy.com/product/ps5/JXHQ37TYYL",
        )
        == "http://login__cr.us%3Bsessid.scoutbb:pass@gw.dataimpulse.com:10001"
    )


def test_bestbuy_net_reset_classified_as_upstream_blocked() -> None:
    """Regression: Akamai connection reset must be UPSTREAM_BLOCKED (proxy FALLBACK)."""
    from scout_api.modules.crawler.services.html_fetcher import (
        classify_camoufox_navigation_error,
    )

    err = classify_camoufox_navigation_error(
        Exception("Page.goto: NS_ERROR_NET_RESET"),
        url="https://www.bestbuy.com/product/playstation-5/JXHQ37TYYL",
    )
    assert err.code == "UPSTREAM_BLOCKED"
    assert err.retryable is True


def test_bestbuy_proxied_launch_enables_geoip(tmp_path: Any) -> None:
    """US-pinned Best Buy exits need geoip so timezone/WebRTC match the proxy IP."""
    captured: dict[str, Any] = {}

    @contextmanager
    def fake_factory(**kwargs: Any) -> Iterator[Any]:
        captured.update(kwargs)

        class FakePage:
            url = "https://www.bestbuy.com/product/ps5/JXHQ37TYYL"

            def goto(self, url: str, **goto_kwargs: Any) -> None:
                del url, goto_kwargs

            def content(self) -> str:
                return "<html><body><h1>ok</h1></body></html>"

            def title(self) -> str:
                return "ok"

            def wait_for_timeout(self, ms: int) -> None:
                del ms

        class FakeBrowser:
            def new_page(self) -> FakePage:
                return FakePage()

        yield FakeBrowser()

    fetcher = CamoufoxHtmlFetcher(
        browser_factory=fake_factory,
        proxy_url="http://u:p@proxy.example:9000",
        settle_ms=0,
        max_settle_attempts=1,
        user_data_dir=tmp_path / "bb-profile",
        warmup_origin=False,
        warm_reuse=False,
    )
    fetcher.fetch("https://www.bestbuy.com/product/ps5/JXHQ37TYYL")
    assert captured["locale"] == "en-US"
    assert captured["geoip"] is True
    username = str(captured.get("proxy", {}).get("username", ""))
    assert "__cr.us" in username
    assert "sessid.scoutbb" in username
    assert captured.get("config", {}).get("disableInstantAnimations") is True


def test_urllib_fetcher_maps_http_403() -> None:
    def opener(request: Any, timeout: int = 30) -> Any:
        del timeout
        raise HTTPError(request.full_url, 403, "Forbidden", hdrs=None, fp=None)

    fetcher = UrllibHtmlFetcher(opener=opener, user_agent="test", timeout=5)
    with pytest.raises(RequestError) as exc:
        fetcher.fetch("https://www.magazineluiza.com.br/p/1")
    assert exc.value.code == "UPSTREAM_BLOCKED"


def test_camoufox_fetcher_returns_html_response(tmp_path: Any) -> None:
    html = (
        "<html><head><title>PS5</title></head><body>"
        "<h1>PlayStation 5</h1>"
        '<script type="application/ld+json">'
        '{"@type":"Product","name":"PlayStation 5","sku":"240590700",'
        '"offers":{"price":"4399.00","availability":"InStock"}}'
        "</script></body></html>"
    )

    class FakePage:
        url = "https://www.magazineluiza.com.br/p/240590700"

        def goto(self, url: str, **kwargs: Any) -> None:
            del url, kwargs

        def content(self) -> str:
            return html

        def title(self) -> str:
            return "PS5"

        def wait_for_timeout(self, ms: int) -> None:
            del ms

    class FakeBrowser:
        def new_page(self) -> FakePage:
            return FakePage()

    @contextmanager
    def fake_factory(**kwargs: Any) -> Iterator[FakeBrowser]:
        del kwargs
        yield FakeBrowser()

    fetcher = CamoufoxHtmlFetcher(
        browser_factory=fake_factory,
        settle_ms=0,
        max_settle_attempts=1,
        user_data_dir=tmp_path / "profile",
        warmup_origin=False,
        warm_reuse=False,
    )
    response = fetcher.fetch("https://www.magazineluiza.com.br/p/240590700")
    assert isinstance(response, HtmlResponse)
    assert "PlayStation 5" in response.text


def test_camoufox_warm_reuses_browser_across_fetches(tmp_path: Any) -> None:
    """Warm reuse must launch once and open a new page per URL."""
    launches = {"n": 0}
    pages = {"n": 0}

    class FakePage:
        url = "https://www.kabum.com.br/produto/1"

        def goto(self, url: str, **kwargs: Any) -> None:
            self.url = url
            del kwargs

        def content(self) -> str:
            return f"<html><body><h1>ok-{self.url}</h1></body></html>"

        def title(self) -> str:
            return "ok"

        def wait_for_timeout(self, ms: int) -> None:
            del ms

        def close(self) -> None:
            return None

    class FakeBrowser:
        def new_page(self) -> FakePage:
            pages["n"] += 1
            return FakePage()

    @contextmanager
    def fake_factory(**kwargs: Any) -> Iterator[FakeBrowser]:
        del kwargs
        launches["n"] += 1
        yield FakeBrowser()

    fetcher = CamoufoxHtmlFetcher(
        browser_factory=fake_factory,
        settle_ms=0,
        max_settle_attempts=1,
        user_data_dir=tmp_path / "warm-profile",
        warmup_origin=False,
        warm_reuse=True,
        warm_max_fetches=10,
    )
    try:
        r1 = fetcher.fetch("https://www.kabum.com.br/produto/1")
        r2 = fetcher.fetch("https://www.kabum.com.br/produto/2")
    finally:
        fetcher.close()
    assert "ok-" in r1.text and "ok-" in r2.text
    assert launches["n"] == 1
    assert pages["n"] == 2
    assert fetcher.browser_launch_count == 1
    assert fetcher.browser_reuse_count == 1


def test_camoufox_fetch_is_safe_from_worker_threads(tmp_path: Any) -> None:
    """Match wave-2 ThreadPool must not trip Playwright greenlet affinity."""
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed

    launches = {"n": 0}
    pages = {"n": 0}
    page_threads: list[str] = []

    class FakePage:
        url = "https://www.kabum.com.br/produto/1"

        def goto(self, url: str, **kwargs: Any) -> None:
            self.url = url
            del kwargs

        def content(self) -> str:
            return f"<html><body><h1>ok-{self.url}</h1></body></html>"

        def title(self) -> str:
            return "ok"

        def wait_for_timeout(self, ms: int) -> None:
            del ms

        def close(self) -> None:
            return None

    class FakeBrowser:
        def new_page(self) -> FakePage:
            pages["n"] += 1
            page_threads.append(threading.current_thread().name)
            return FakePage()

    @contextmanager
    def fake_factory(**kwargs: Any) -> Iterator[FakeBrowser]:
        del kwargs
        launches["n"] += 1
        yield FakeBrowser()

    fetcher = CamoufoxHtmlFetcher(
        browser_factory=fake_factory,
        settle_ms=0,
        max_settle_attempts=1,
        user_data_dir=tmp_path / "thread-profile",
        warmup_origin=False,
        warm_reuse=True,
        warm_max_fetches=20,
    )
    urls = [f"https://www.kabum.com.br/produto/{i}" for i in range(1, 5)]
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(fetcher.fetch, url) for url in urls]
            texts = [fut.result().text for fut in as_completed(futures)]
    finally:
        fetcher.close()

    assert len(texts) == 4
    assert all("ok-" in t for t in texts)
    assert launches["n"] == 1
    assert pages["n"] == 4
    # All Playwright calls must share the camoufox-owner thread.
    assert len(set(page_threads)) == 1
    assert page_threads[0].startswith("camoufox-owner")


def test_camoufox_oneshot_drops_warm_before_nested_launch(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AliExpress oneshot must close warm first (no nested Sync Camoufox)."""
    launches = {"n": 0}
    closed = {"n": 0}

    class FakePage:
        url = "https://www.kabum.com.br/produto/1"

        def goto(self, url: str, **kwargs: Any) -> None:
            self.url = url
            del kwargs

        def content(self) -> str:
            return f"<html><body><h1>ok-{self.url}</h1></body></html>"

        def title(self) -> str:
            return "ok"

        def wait_for_timeout(self, ms: int) -> None:
            del ms

        def close(self) -> None:
            return None

    class FakeBrowser:
        def new_page(self) -> FakePage:
            return FakePage()

    @contextmanager
    def fake_factory(**kwargs: Any) -> Iterator[FakeBrowser]:
        del kwargs
        launches["n"] += 1
        try:
            yield FakeBrowser()
        finally:
            closed["n"] += 1

    fetcher = CamoufoxHtmlFetcher(
        browser_factory=fake_factory,
        settle_ms=0,
        max_settle_attempts=1,
        user_data_dir=tmp_path / "oneshot-profile",
        warmup_origin=False,
        warm_reuse=True,
        warm_max_fetches=20,
    )

    def _navigate_without_payload_wait(
        page: FakePage, url: str, captured: dict[str, str]
    ) -> None:
        del captured
        page.goto(url)

    # Lifecycle is the subject of this test; avoid the production 25-second
    # AliExpress payload wait because this fake intentionally emits no payload.
    monkeypatch.setattr(
        fetcher, "_goto_aliexpress_early_stop", _navigate_without_payload_wait
    )
    try:
        fetcher.fetch("https://www.kabum.com.br/produto/1")
        assert launches["n"] == 1
        # AliExpress forces oneshot temp profile → must drop warm first.
        # Fake HTML will not yield MTop payload; we only assert launch/close.
        with pytest.raises(RequestError):
            fetcher.fetch("https://pt.aliexpress.com/item/1005001.html")
        assert launches["n"] == 2
        assert closed["n"] >= 1
        # Next BR fetch cold-starts again after oneshot.
        fetcher.fetch("https://www.kabum.com.br/produto/2")
        assert launches["n"] == 3
    finally:
        fetcher.close()


def test_camoufox_keeps_warm_after_upstream_blocked(tmp_path: Any) -> None:
    """Classified UPSTREAM_BLOCKED must not force a relaunch (ADR 0032 reuse)."""
    launches = {"n": 0}
    pages = {"n": 0}
    mode = {"challenge": True}

    class FakePage:
        url = "https://www.kabum.com.br/produto/1"

        def goto(self, url: str, **kwargs: Any) -> None:
            self.url = url
            del kwargs

        def content(self) -> str:
            if mode["challenge"]:
                return "<html><body>Performing security verification</body></html>"
            return f"<html><body><h1>ok-{self.url}</h1></body></html>"

        def title(self) -> str:
            return "Just a moment..." if mode["challenge"] else "ok"

        def wait_for_timeout(self, ms: int) -> None:
            del ms

        def close(self) -> None:
            return None

    class FakeBrowser:
        def new_page(self) -> FakePage:
            pages["n"] += 1
            return FakePage()

    @contextmanager
    def fake_factory(**kwargs: Any) -> Iterator[FakeBrowser]:
        del kwargs
        launches["n"] += 1
        yield FakeBrowser()

    fetcher = CamoufoxHtmlFetcher(
        browser_factory=fake_factory,
        settle_ms=0,
        max_settle_attempts=1,
        user_data_dir=tmp_path / "keep-warm",
        warmup_origin=False,
        warm_reuse=True,
        warm_max_fetches=10,
    )
    try:
        with pytest.raises(RequestError) as exc:
            fetcher.fetch("https://www.kabum.com.br/produto/1")
        assert exc.value.code == "UPSTREAM_BLOCKED"
        mode["challenge"] = False
        ok = fetcher.fetch("https://www.kabum.com.br/produto/2")
    finally:
        fetcher.close()

    assert "ok-" in ok.text
    assert launches["n"] == 1
    assert pages["n"] == 2
    assert fetcher.browser_launch_count == 1
    assert fetcher.browser_reuse_count == 1


def test_camoufox_fetcher_raises_when_challenge_persists(tmp_path: Any) -> None:
    class FakePage:
        url = "https://nissei.com/py/x"

        def goto(self, url: str, **kwargs: Any) -> None:
            del url, kwargs

        def content(self) -> str:
            return "<html><body>Performing security verification</body></html>"

        def title(self) -> str:
            return "Just a moment..."

        def wait_for_timeout(self, ms: int) -> None:
            del ms

    class FakeBrowser:
        def new_page(self) -> FakePage:
            return FakePage()

    @contextmanager
    def fake_factory(**kwargs: Any) -> Iterator[FakeBrowser]:
        del kwargs
        yield FakeBrowser()

    fetcher = CamoufoxHtmlFetcher(
        browser_factory=fake_factory,
        settle_ms=0,
        max_settle_attempts=2,
        user_data_dir=tmp_path / "profile",
        warmup_origin=False,
        warm_reuse=False,
    )
    with pytest.raises(RequestError) as exc:
        fetcher.fetch("https://nissei.com/py/x")
    assert exc.value.code == "UPSTREAM_BLOCKED"
    assert exc.value.retryable is True


def test_camoufox_fetcher_raises_non_retryable_on_hard_block(tmp_path: Any) -> None:
    class FakePage:
        url = "https://nissei.com/py/x"

        def goto(self, url: str, **kwargs: Any) -> None:
            del url, kwargs

        def content(self) -> str:
            return "<html><h1>Sorry, you have been blocked</h1></html>"

        def title(self) -> str:
            return "Attention Required! | Cloudflare"

        def wait_for_timeout(self, ms: int) -> None:
            del ms

    class FakeBrowser:
        def new_page(self) -> FakePage:
            return FakePage()

    @contextmanager
    def fake_factory(**kwargs: Any) -> Iterator[FakeBrowser]:
        del kwargs
        yield FakeBrowser()

    fetcher = CamoufoxHtmlFetcher(
        browser_factory=fake_factory,
        settle_ms=0,
        max_settle_attempts=1,
        user_data_dir=tmp_path / "profile",
        warmup_origin=False,
        warm_reuse=False,
    )
    with pytest.raises(RequestError) as exc:
        fetcher.fetch("https://nissei.com/py/x")
    assert exc.value.code == "UPSTREAM_BLOCKED"
    assert exc.value.retryable is False
    assert "proxy" in exc.value.args[0].lower()


def test_camoufox_launch_kwargs_include_proxy_profile_and_coop(tmp_path: Any) -> None:
    captured: dict[str, Any] = {}

    @contextmanager
    def fake_factory(**kwargs: Any) -> Iterator[Any]:
        captured.update(kwargs)

        class FakePage:
            url = "https://nissei.com/py/x"
            gotos: list[str] = []

            def goto(self, url: str, **goto_kwargs: Any) -> None:
                self.gotos.append(url)
                del goto_kwargs

            def content(self) -> str:
                return "<html><body><h1>ok</h1></body></html>"

            def title(self) -> str:
                return "ok"

            def wait_for_timeout(self, ms: int) -> None:
                del ms

        class FakeBrowser:
            def new_page(self) -> FakePage:
                return FakePage()

        yield FakeBrowser()

    fetcher = CamoufoxHtmlFetcher(
        browser_factory=fake_factory,
        proxy_url="http://u:p@proxy.example:9000",
        settle_ms=0,
        max_settle_attempts=1,
        user_data_dir=tmp_path / "profile",
        warmup_origin=True,
        warm_reuse=False,
    )
    fetcher.fetch("https://nissei.com/py/x")
    assert captured["proxy"] == {
        "server": "http://proxy.example:9000",
        "username": "u",
        "password": "p",
    }
    assert captured["locale"] == "es-PY"
    assert captured["geoip"] is False
    assert captured["persistent_context"] is True
    assert captured["disable_coop"] is True
    assert captured["user_data_dir"] == str(tmp_path / "profile")


def test_camoufox_profile_dir_falls_back_when_unwritable(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocked = tmp_path / "blocked" / "profile"
    real_mkdir = Path.mkdir

    def selective_mkdir(
        self: Path,
        mode: int = 0o777,
        parents: bool = False,
        exist_ok: bool = False,
    ) -> None:
        if self == blocked:
            raise PermissionError("denied")
        return real_mkdir(self, mode=mode, parents=parents, exist_ok=exist_ok)

    monkeypatch.setattr(Path, "mkdir", selective_mkdir)

    captured: dict[str, Any] = {}

    @contextmanager
    def fake_factory(**kwargs: Any) -> Iterator[Any]:
        captured.update(kwargs)

        class FakePage:
            url = "https://www.magazineluiza.com.br/p/1"

            def goto(self, url: str, **goto_kwargs: Any) -> None:
                del url, goto_kwargs

            def content(self) -> str:
                return "<html><body><h1>ok</h1></body></html>"

            def title(self) -> str:
                return "ok"

            def wait_for_timeout(self, ms: int) -> None:
                del ms

        class FakeBrowser:
            def new_page(self) -> FakePage:
                return FakePage()

        yield FakeBrowser()

    fetcher = CamoufoxHtmlFetcher(
        browser_factory=fake_factory,
        user_data_dir=blocked,
        settle_ms=0,
        max_settle_attempts=1,
        warmup_origin=False,
        warm_reuse=False,
    )
    fetcher.fetch("https://www.magazineluiza.com.br/p/1")

    assert (
        Path(captured["user_data_dir"])
        .as_posix()
        .endswith("scout-api-camoufox-profiles/default")
    )
    html = (
        b"<html><body><h1>PlayStation 5</h1>"
        b'<script type="application/ld+json">'
        b'{"@type":"Product","name":"PlayStation 5","sku":"240590700",'
        b'"offers":{"price":"4399.00","availability":"https://schema.org/InStock"}}'
        b"</script>"
        b"<button>Adicionar \xc3\xa0 sacola</button></body></html>"
    )
    url = "https://www.magazineluiza.com.br/p/240590700"

    class FakeFetcher:
        def fetch(self, fetch_url: str) -> HtmlResponse:
            return HtmlResponse(
                fetch_url,
                body=html,
                encoding="utf-8",
                request=Request(fetch_url),
            )

    item = ProductScrapeService(
        fetcher=FakeFetcher(),
        guard=ScrapeGuard(
            url_cooldown_seconds=1,
            domain_min_interval_seconds=0,
            result_cache_ttl_seconds=1,
        ),
    ).scrape(url)
    assert item.product_id == "240590700"
    assert item.price == Decimal("4399.00")
    assert item.store == "magazineluiza"


def test_product_scrape_service_supports_nissei_domain() -> None:
    html = (
        b"<html><body><h1>Placa Madre</h1>"
        b'<meta itemprop="price" content="3227000">'
        b"<span>SKU 148321</span><span>En stock</span></body></html>"
    )
    url = (
        "https://nissei.com/py/informatica/placa-madre-gigabyte-am5-x870-aorus-"
        "stealth-ice-hdmi-usb3-2-4m-2-ddr5-atx"
    )

    class FakeFetcher:
        def fetch(self, fetch_url: str) -> HtmlResponse:
            return HtmlResponse(
                fetch_url,
                body=html,
                encoding="utf-8",
                request=Request(fetch_url),
            )

    item = ProductScrapeService(
        fetcher=FakeFetcher(),
        guard=ScrapeGuard(
            url_cooldown_seconds=1,
            domain_min_interval_seconds=0,
            result_cache_ttl_seconds=1,
        ),
    ).scrape(url)
    assert item.store == "nissei"
    assert item.sku == "148321"
    assert item.price == Decimal("3227000")


def test_camoufox_intercepts_shopee_get_pc_network_response(tmp_path: Any) -> None:
    url = "https://shopee.com.br/Kingston-i.341936748.29277977480"
    payload = (
        '{"data":{"item":{"item_id":29277977480,"shop_id":341936748,'
        '"title":"Kingston","price":159900000,"models":[]}}}'
    )

    @contextmanager
    def fake_factory(**kwargs: Any) -> Iterator[Any]:
        del kwargs

        class FakeResponse:
            url = (
                "https://shopee.com.br/api/v4/pdp/get_pc"
                "?shop_id=341936748&item_id=29277977480"
            )
            status = 200

            def text(self) -> str:
                return payload

        class FakePage:
            url = "https://shopee.com.br/verify/traffic/error"
            _handlers: list[Any]

            def __init__(self) -> None:
                self._handlers = []

            def on(self, event: str, handler: Any) -> None:
                if event == "response":
                    self._handlers.append(handler)

            def goto(self, target: str, **goto_kwargs: Any) -> None:
                del goto_kwargs
                self.url = target
                for handler in self._handlers:
                    handler(FakeResponse())

            def content(self) -> str:
                return "<html><title>verify</title><body>verify/traffic</body></html>"

            def title(self) -> str:
                return "verify"

            def wait_for_timeout(self, ms: int) -> None:
                del ms

        class FakeBrowser:
            def new_page(self) -> FakePage:
                return FakePage()

        yield FakeBrowser()

    fetcher = CamoufoxHtmlFetcher(
        browser_factory=fake_factory,
        settle_ms=0,
        max_settle_attempts=2,
        user_data_dir=tmp_path / "profile",
        warmup_origin=False,
        warm_reuse=False,
    )
    response = fetcher.fetch(url)
    assert "data-shopee-pdp" in response.text
    assert "29277977480" in response.text
    assert response.url == url
