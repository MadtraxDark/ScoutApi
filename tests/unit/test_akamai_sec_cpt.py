"""Akamai sec-cpt proof-of-work and browser resolution budget."""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

import pytest

from scout_api.modules.crawler.services.akamai_sec_cpt import (
    generate_sec_cpt_answers,
    parse_sec_cpt,
    sec_cpt_prefix,
    sec_cpt_satisfied,
)
from scout_api.modules.crawler.services.challenge_resolution import ChallengeResolver

_CHALLENGE = (
    "<html><body><div id='sec-if-cpt-container'>"
    "<div class='behavioral-content'></div></div></body></html>"
)
_PDP = (
    "<html><body><h1>Produto</h1>"
    "<script id='__NEXT_DATA__'>"
    '{"props":{"pageProps":{"data":{"item":{"id":"1"}}}}}'
    "</script></body></html>"
)


def _crypto_html(*, duration: int = 0) -> str:
    payload = {
        "provider": "crypto",
        "token": "token-live",
        "timestamp": 1713283747,
        "nonce": "ebccdb479fcb92636fbc",
        "difficulty": 1,
        "count": 1,
        "chlg_duration": duration,
    }
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    return (
        "<html><body><iframe id='sec-cpt-if' provider='crypto' "
        f"challenge='{encoded}' data-duration='{duration}' "
        "src='/_sec/cp_challenge/crypto_message.htm'></iframe></body></html>"
    )


def test_parse_crypto_iframe_and_cookie_markers() -> None:
    parsed = parse_sec_cpt(_crypto_html(duration=5))
    assert parsed is not None
    assert parsed.provider == "crypto"
    assert parsed.needs_proof_of_work is True
    assert parsed.duration_s == 5
    assert parsed.difficulty == 1
    assert sec_cpt_prefix("abc~1~x") == "abc"
    assert sec_cpt_prefix("no-tilde") is None
    assert sec_cpt_satisfied("abc~3~1") is True
    assert sec_cpt_satisfied("abc~1~1") is False


def test_parse_behavioral_json_has_no_proof() -> None:
    body = json.dumps(
        {
            "sec-cp-challenge": "true",
            "provider": "behavioral",
            "verify_url": "/dynamic",
        }
    )
    parsed = parse_sec_cpt(body)
    assert parsed is not None
    assert parsed.provider == "behavioral"
    assert parsed.needs_proof_of_work is False


def test_pow_answer_satisfies_difficulty() -> None:
    answers = generate_sec_cpt_answers(
        sec="abc",
        timestamp=11,
        nonce="n",
        difficulty=1,
        count=1,
    )
    assert answers is not None
    assert len(answers) == 1
    material = f"abc11n1{answers[0]}"
    digest = hashlib.sha256(material.encode("ascii")).digest()
    assert int.from_bytes(digest, "big") % 1 == 0
    assert (
        generate_sec_cpt_answers(
            sec="",
            timestamp=1,
            nonce="n",
            difficulty=1,
            count=1,
        )
        is None
    )


def test_behavioral_resolver_does_not_reload_before_clearance() -> None:
    class _Mouse:
        def move(self, *_args: Any, **_kwargs: Any) -> None:
            return None

        def down(self) -> None:
            return None

        def up(self) -> None:
            return None

    class _Page:
        def __init__(self) -> None:
            self.mouse = _Mouse()
            self.viewport_size = {"width": 800, "height": 600}
            self.gotos = 0

        def content(self) -> str:
            return _CHALLENGE

        def title(self) -> str:
            return ""

        def url(self) -> str:
            return "https://www.magazineluiza.com.br/busca/cooler/"

        def wait_for_timeout(self, _ms: int) -> None:
            return None

        def locator(self, _selector: str) -> Any:
            class _Loc:
                first = self

                def count(self) -> int:
                    return 0

                def bounding_box(self) -> None:
                    return None

            return _Loc()

        def goto(self, *_args: Any, **_kwargs: Any) -> None:
            self.gotos += 1

    page = _Page()
    resolver = ChallengeResolver(soft_wait_ms=1_000, max_attempts=2)
    assert (
        resolver.try_resolve(
            page,
            html=_CHALLENGE,
            title="",
            page_url=page.url(),
            resume_url=page.url(),
        )
        is False
    )
    assert page.gotos == 0


def test_resume_navigation_runs_only_after_sec_cpt_marker() -> None:
    class _Mouse:
        def move(self, *_args: Any, **_kwargs: Any) -> None:
            return None

        def down(self) -> None:
            return None

        def up(self) -> None:
            return None

    class _Context:
        def cookies(self) -> list[dict[str, str]]:
            return [{"name": "sec_cpt", "value": "abc~3~ok"}]

    class _Page:
        def __init__(self) -> None:
            self.mouse = _Mouse()
            self.viewport_size = {"width": 800, "height": 600}
            self.context = _Context()
            self.gotos = 0

        def content(self) -> str:
            return _PDP if self.gotos else _CHALLENGE

        def title(self) -> str:
            return "Produto" if self.gotos else ""

        def url(self) -> str:
            return "https://www.magazineluiza.com.br/p/1"

        def wait_for_timeout(self, _ms: int) -> None:
            return None

        def locator(self, _selector: str) -> Any:
            class _Loc:
                def count(self) -> int:
                    return 0

                def bounding_box(self) -> None:
                    return None

                @property
                def first(self) -> _Loc:
                    return self

            return _Loc()

        def goto(self, *_args: Any, **_kwargs: Any) -> None:
            self.gotos += 1

    page = _Page()
    resolver = ChallengeResolver(soft_wait_ms=1_000, max_attempts=1)
    assert resolver.try_resolve(
        page,
        html=_CHALLENGE,
        title="",
        page_url=page.url(),
        resume_url=page.url(),
    )
    assert page.gotos == 1


def test_magalu_http_crypto_accepts_cleared_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scrapy.http import HtmlResponse, Request

    from scout_api.modules.crawler.services import magalu_http_first_fetcher as mod

    solved = HtmlResponse(
        MAGALU_SEARCH,
        body=(
            b"<html><body><a data-testid='product-card-link' "
            b"href='/cooler/p/abc123/'>Cooler</a></body></html>"
        ),
        encoding="utf-8",
        request=Request(MAGALU_SEARCH),
    )
    solved.meta["fetch_metrics"] = {"fetch_strategy": "http-sec-cpt-crypto"}

    def _fake_fetch(url: str, **_kwargs: Any) -> HtmlResponse:
        assert url == MAGALU_SEARCH
        return solved

    monkeypatch.setattr(mod, "fetch_after_http_crypto_sec_cpt", _fake_fetch)

    class _Http:
        def fetch(self, url: str) -> HtmlResponse:
            del url
            return HtmlResponse(
                MAGALU_SEARCH,
                body=_crypto_html().encode(),
                encoding="utf-8",
                request=Request(MAGALU_SEARCH),
            )

    class _Browser:
        def __init__(self) -> None:
            self.calls = 0

        def fetch(self, url: str) -> HtmlResponse:
            self.calls += 1
            return HtmlResponse(url, body=b"<html></html>", request=Request(url))

    browser = _Browser()
    response = mod.MagaluHttpFirstHtmlFetcher(http=_Http(), browser=browser).fetch(
        MAGALU_SEARCH
    )
    assert browser.calls == 0
    assert response.meta["fetch_metrics"]["fetch_strategy"] == "http-sec-cpt-crypto"


MAGALU_SEARCH = "https://www.magazineluiza.com.br/busca/cooler/"
