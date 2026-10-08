"""Manual browser scenario against a real, isolated PostgreSQL catalog.

Requires TEST_ACTIVITY_DATABASE_URL (scout_activity_test*) and API on 8012.
The existing localhost:3000 frontend is routed to that isolated API for this
dedicated browser context only. Store data/history are never mocked.
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import expect, sync_playwright
from sqlalchemy import create_engine, delete
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from scout_api.modules.auth.deps import DEV_BYPASS_USER_ID
from scout_api.modules.auth.schemas import AuthenticatedPrincipal
from scout_api.modules.matching.activity_search import ActivityFilters
from scout_api.modules.matching.activity_service import ActivityService
from scout_api.modules.matching.models import CanonicalProduct
from scout_api.modules.matching.repository import MatchingRepository


def main() -> None:
    address = os.environ["TEST_ACTIVITY_DATABASE_URL"]
    parsed = make_url(address)
    if parsed.host not in {"localhost", "127.0.0.1"} or not (
        parsed.database or ""
    ).startswith("scout_activity_test"):
        raise RuntimeError("Exige PostgreSQL local isolado scout_activity_test*")
    engine = create_engine(address)
    api_origin = os.environ.get("TEST_FRONTEND_API_ORIGIN", "http://localhost:8000")
    isolated_api = "http://localhost:8012"
    frontend = "http://localhost:3000"
    artifacts = Path(".tmp/catalog-changes-browser")
    artifacts.mkdir(parents=True, exist_ok=True)
    now = datetime(2026, 10, 7, 15, tzinfo=UTC)
    product_ids = []
    try:
        with Session(engine) as session:
            repo = MatchingRepository(session)
            product, _ = repo.get_or_create_canonical(
                title="Câmera isolada de investigação",
                brand="Élite",
                model="Móvel",
                owner_user_id=DEV_BYPASS_USER_ID,
            )
            product.created_at = now - timedelta(days=1)
            listing, _ = repo.get_or_create_listing(
                canonical=product,
                store="kabum",
                country="BR",
                product_id=uuid4().hex,
                url="https://www.kabum.com.br/produto/test-isolated-changes",
                canonical_url="https://www.kabum.com.br/produto/test-isolated-changes",
            )
            for index in range(31):
                repo.append_event(
                    listing,
                    "price_changed",
                    before={"price": "100", "currency": "BRL"},
                    after={"price": "90", "currency": "BRL"},
                    detected_at=now + timedelta(seconds=index),
                )
            repo.append_event(
                listing,
                "seller_changed",
                before={"seller": "São José"},
                after={"seller": "Élite Vendas"},
                detected_at=now + timedelta(minutes=1),
            )
            repo.append_event(
                listing,
                "future_event",
                after={"error": "SECRET"},
                detected_at=now + timedelta(minutes=2),
            )
            private, _ = repo.get_or_create_canonical(
                title="Produto privado de investigação isolada", owner_user_id=uuid4()
            )
            session.commit()
            product_ids = [product.id, private.id]
            started = time.perf_counter()
            result = ActivityService(session).list_changes(
                viewer=AuthenticatedPrincipal(id=DEV_BYPASS_USER_ID),
                filters=ActivityFilters(q="CAMERA elite movel jose kabum"),
                limit=1,
            )
            query_ms = (time.perf_counter() - started) * 1000
            assert len(result.items) == 1 and result.items[0].type == "seller_changed"
            assert result.next_cursor is None

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            context = browser.new_context(
                viewport={"width": 1440, "height": 1000},
                timezone_id="America/Sao_Paulo",
            )
            errors = []
            page = context.new_page()
            page.set_default_timeout(10000)
            page.on("pageerror", lambda error: errors.append(str(error)))
            change_requests = []
            page.on(
                "request",
                lambda request: change_requests.append(request.url)
                if "/products/changes" in request.url
                else None,
            )

            def forward(route):
                # Transport rewrite only: all business responses use the real API.
                response = route.fetch(
                    url=route.request.url.replace(api_origin, isolated_api, 1)
                )
                route.fulfill(response=response)

            context.route(f"{api_origin}/**", forward)
            page.goto(f"{frontend}/admin/catalogo/alteracoes")
            expect(
                page.get_by_role("heading", name="Alterações", exact=True)
            ).to_be_visible(timeout=60000)
            expect(page.get_by_text("25 eventos carregados", exact=True)).to_be_visible(
                timeout=15000
            )
            page.get_by_role("button", name="Carregar mais eventos", exact=True).click()
            expect(
                page.get_by_text("34 eventos carregados", exact=True)
            ).to_be_visible()
            expect(
                page.get_by_text("Fim dos resultados desta pesquisa.", exact=True)
            ).to_be_visible()
            assert "SECRET" not in page.locator("main").inner_text()
            assert "Produto privado" not in page.locator("main").inner_text()
            page.get_by_role(
                "combobox", name="Tipo de evento", exact=True
            ).select_option("seller_changed")
            page.get_by_role("combobox", name="Loja", exact=True).select_option("kabum")
            page.get_by_label("De", exact=True).fill("2026-10-07")
            page.get_by_label("Até", exact=True).fill("2026-10-07")
            page.get_by_role("button", name="Pesquisar", exact=True).click()
            expect(page.get_by_text("1 evento carregado", exact=True)).to_be_visible()
            assert "event_type=seller_changed" in page.url
            assert "from=2026-10-07" in page.url
            search = page.get_by_label("Buscar no histórico", exact=True)
            request_count = len(change_requests)
            search.click()
            search.press_sequentially("CAMERA elite movel jose kabum", delay=10)
            expect(page.locator("mark").filter(has_text="Câmera")).to_have_count(1)
            expect(search).to_be_focused()
            assert len(change_requests) == request_count + 1
            assert search.evaluate("el => el.selectionStart") == len(
                "CAMERA elite movel jose kabum"
            )
            assert "event_type=seller_changed" in page.url
            assert "cursor=" not in change_requests[-1]
            assert {"Câmera", "Élite", "Móvel", "José", "KaBuM"}.issubset(
                set(page.locator("mark").all_text_contents())
            )
            page.get_by_text("Ver evidências históricas", exact=True).click()
            expect(page.get_by_role("table")).to_be_visible()
            page.screenshot(path=str(artifacts / "search-desktop.png"), full_page=True)
            page.reload()
            expect(page.get_by_text("1 evento carregado", exact=True)).to_be_visible()
            expect(page.get_by_label("Buscar no histórico", exact=True)).to_have_value(
                "CAMERA elite movel jose kabum"
            )
            for width in (320, 768, 1024, 1440):
                page.set_viewport_size({"width": width, "height": 1000})
                page.get_by_text("Ver evidências históricas", exact=True).click()
                if not page.evaluate(
                    "document.documentElement.scrollWidth <= window.innerWidth"
                ):
                    print(
                        page.evaluate(
                            "Array.from(document.querySelectorAll('main *'))"
                            ".filter(el => el.getBoundingClientRect().right"
                            " > window.innerWidth)"
                            ".map(el => ({tag:el.tagName, cls:el.className,"
                            " width:el.getBoundingClientRect().width})).slice(0,12)"
                        ),
                        flush=True,
                    )
                    page.screenshot(
                        path=str(artifacts / "overflow.png"), full_page=True
                    )
                assert page.evaluate(
                    "document.documentElement.scrollWidth <= window.innerWidth"
                ), width
                page.screenshot(
                    path=str(artifacts / f"search-{width}.png"), full_page=True
                )
                page.get_by_text("Ver evidências históricas", exact=True).click()
            page.get_by_label("Buscar no histórico", exact=True).fill(
                "inexistente-isolado"
            )
            page.get_by_role("button", name="Pesquisar", exact=True).click()
            expect(
                page.get_by_role(
                    "heading", name="Nenhuma alteração encontrada", exact=True
                )
            ).to_be_visible()
            page.go_back()
            expect(page.get_by_text("1 evento carregado", exact=True)).to_be_visible()
            page.get_by_role(
                "link", name="Câmera isolada de investigação", exact=True
            ).click()
            expect(page).to_have_url(f"{frontend}/admin/produtos/{product_ids[0]}")
            page.go_back()
            expect(page.get_by_text("1 evento carregado", exact=True)).to_be_visible()

            failed = False

            def fail_once(route):
                nonlocal failed
                if not failed:
                    failed = True
                    route.fulfill(
                        status=503,
                        content_type="application/json",
                        body=json.dumps(
                            {
                                "detail": {
                                    "code": "DATABASE_UNAVAILABLE",
                                    "message": "Falha isolada de teste",
                                    "retryable": True,
                                }
                            }
                        ),
                        headers={
                            "access-control-allow-origin": frontend,
                            "access-control-allow-credentials": "true",
                        },
                    )
                else:
                    forward(route)

            # Explicit error fixture; no fabricated catalog records.
            page.route(f"{api_origin}/products/changes*", fail_once)
            page.reload()
            expect(
                page.get_by_role("region", name="Histórico registrado").get_by_role(
                    "alert"
                )
            ).to_be_visible()
            page.get_by_role("button", name="Tentar novamente", exact=True).click()
            expect(page.get_by_text("1 evento carregado", exact=True)).to_be_visible()
            assert not errors, errors
            (artifacts / "result.json").write_text(
                json.dumps(
                    {
                        "postgres_query_ms": round(query_ms, 2),
                        "browser_errors": errors,
                        "checks": [
                            "real PostgreSQL search",
                            "pagination 25 to 34",
                            "accent/case highlights",
                            "incremental debounce/focus/cursor reset",
                            "history details",
                            "reload",
                            "empty",
                            "back",
                            "product link",
                            "error/retry",
                            "320/768/1024/1440",
                        ],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            browser.close()
            print(
                json.dumps(
                    {
                        "status": "passed",
                        "postgres_query_ms": round(query_ms, 2),
                        "artifacts": str(artifacts),
                    }
                ),
                flush=True,
            )
    finally:
        with engine.begin() as connection:
            connection.execute(
                delete(CanonicalProduct).where(CanonicalProduct.id.in_(product_ids))
            )
        engine.dispose()


if __name__ == "__main__":
    main()
