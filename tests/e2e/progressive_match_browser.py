"""Browser + real API/PostgreSQL fixture; run manually against isolated servers."""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from tests.unit.test_match_run_progressive import _hit

from scout_api.modules.auth.deps import DEV_BYPASS_USER_ID
from scout_api.modules.matching.match_run_service import MatchRunService
from scout_api.modules.matching.match_run_staging import stage_hit
from scout_api.modules.matching.models import (
    CanonicalProduct,
    OfferSnapshot,
    ProductMatchRun,
    StoreListing,
)


def main() -> None:
    address = os.environ["TEST_DATABASE_URL"]
    if make_url(address).database != "scout_progressive_test":
        raise RuntimeError("Exige banco isolado scout_progressive_test")
    engine = create_engine(address)
    factory = sessionmaker(engine, expire_on_commit=False)
    artifacts = Path(".tmp/progressive-browser")
    artifacts.mkdir(parents=True, exist_ok=True)
    with factory() as session:
        product = CanonicalProduct(
            title="Produto E2E progressivo", owner_user_id=DEV_BYPASS_USER_ID
        )
        session.add(product)
        session.flush()
        run = ProductMatchRun(
            product_id=product.id,
            requested_by=DEV_BYPASS_USER_ID,
            status="running",
            worker_id="browser-fixture",
            attempts=1,
            claim_expires_at=datetime.now(UTC) + timedelta(minutes=30),
        )
        session.add(run)
        session.flush()
        service = MatchRunService(session)
        service.record_targets(
            run, stores=["amazon_br", "kabum", "terabyte"], reference_payload={}
        )
        service.record_store_started(run, store="amazon_br")
        session.commit()
        product_id, run_id = product.id, run.id

    def outcome(store: str, decision: str = "auto_match") -> None:
        hit = _hit(decision).model_copy(update={"store": store})
        external_id = str(uuid.uuid4())
        domain = {
            "amazon_br": "www.amazon.com.br",
            "kabum": "www.kabum.com.br",
            "terabyte": "www.terabyteshop.com.br",
            "pichau": "www.pichau.com.br",
        }[store]
        public_url = f"https://{domain}/product/{external_id}"
        hit.product = hit.product.model_copy(
            update={
                "store": "amazon" if store == "amazon_br" else store,
                "product_id": external_id,
                "title": f"Oferta E2E {store}",
                "url": public_url,
                "canonical_url": public_url,
            }
        )
        with factory() as session:
            run = session.get(ProductMatchRun, run_id)
            MatchRunService(session).apply_store_outcome(
                run,
                store=store,
                display_name=store,
                status="match",
                duration_ms=10,
                queries=[],
                candidates_found=1,
                candidates_evaluated=1,
                matched_decision=decision,
                matched_payload=stage_hit(hit),
                matched_price=hit.product.pix_price,
                matched_currency="BRL",
                matched_title=hit.product.title,
                matched_url=hit.product.url,
                expected_worker_id="browser-fixture",
                expected_attempts=1,
            )
            session.commit()

    requests: list[dict] = []
    errors: list[str] = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1100})
            page.on(
                "response",
                lambda response: requests.append(
                    {"url": response.url, "status": response.status}
                ),
            )
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://localhost:3011/admin/produtos/{product_id}")
            expect(
                page.get_by_role("heading", name="Produto E2E progressivo", exact=True)
            ).to_be_visible(timeout=60000)
            expect(
                page.get_by_role("region", name="Progresso da busca por loja")
            ).to_be_visible(timeout=15000)
            outcome("amazon_br")
            committed_at = time.monotonic()
            expect(page.get_by_text("Oferta E2E amazon_br", exact=True)).to_be_visible(
                timeout=10000
            )
            latency = time.monotonic() - committed_at
            expect(
                page.get_by_text(
                    "Resultado parcial — encontrado nesta busca", exact=True
                )
            ).to_have_count(1)
            outcome("kabum", "review")
            expect(page.get_by_text("Requer revisão", exact=True)).to_be_visible(
                timeout=10000
            )
            expect(page.get_by_text("Oferta E2E kabum", exact=True)).to_have_count(0)
            outcome("terabyte")
            expect(page.get_by_text("Oferta E2E terabyte", exact=True)).to_be_visible(
                timeout=10000
            )
            expect(
                page.get_by_text(
                    "Resultado parcial — encontrado nesta busca", exact=True
                )
            ).to_have_count(2)
            page.reload()
            expect(page.get_by_text("Oferta E2E amazon_br", exact=True)).to_be_visible(
                timeout=15000
            )
            name = page.get_by_label("Nome canônico", exact=True)
            name.fill("Rascunho preservado")
            page.screenshot(path=str(artifacts / "partial.png"), full_page=True)
            with factory() as session:
                run = session.get(ProductMatchRun, run_id)
                for row in run.store_runs:
                    if row.matched_payload:
                        item = row.matched_payload["product"]
                        listing = StoreListing(
                            canonical_product_id=product_id,
                            store=item["store"],
                            country=item["country"],
                            product_id=item["product_id"],
                            url=item["url"],
                            canonical_url=item["canonical_url"],
                            title=item["title"],
                            match_decision=row.matched_decision,
                            confidence=Decimal("0.97"),
                        )
                        session.add(listing)
                        session.flush()
                        row.matched_listing_id = listing.id
                        session.add(
                            OfferSnapshot(
                                listing_id=listing.id,
                                price=row.matched_price,
                                currency="BRL",
                                available=True,
                                fingerprint=str(uuid.uuid4()),
                                payload=item,
                            )
                        )
                run.status = "completed"
                run.finished_at = datetime.now(UTC)
                session.commit()
            expect(
                page.get_by_text(
                    "Resultado parcial — encontrado nesta busca", exact=True
                )
            ).to_have_count(0, timeout=15000)
            expect(
                page.get_by_text(
                    "Resultado ainda não reconciliado com o catálogo", exact=True
                )
            ).to_have_count(0)
            expect(name).to_have_value("Rascunho preservado")
            expect(
                page.get_by_text("Correspondência em revisão", exact=True)
            ).to_be_visible()
            page.screenshot(path=str(artifacts / "terminal.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            page.screenshot(path=str(artifacts / "mobile.png"), full_page=True)
            # A new job can fail after durable results; navigation does not cancel it.
            with factory() as session:
                second_run = ProductMatchRun(
                    product_id=product_id,
                    requested_by=DEV_BYPASS_USER_ID,
                    status="running",
                    worker_id="browser-fixture",
                    attempts=1,
                    claim_expires_at=datetime.now(UTC) + timedelta(minutes=30),
                )
                session.add(second_run)
                session.flush()
                run_id = second_run.id
                session.commit()
            outcome("pichau")
            page.goto("http://localhost:3011/admin?view=products")
            page.goto(f"http://localhost:3011/admin/produtos/{product_id}")
            expect(page.get_by_text("Oferta E2E pichau", exact=True)).to_be_visible(
                timeout=15000
            )
            with factory() as session:
                run = session.get(ProductMatchRun, run_id)
                assert run.status == "running"
                run.status = "failed"
                run.failure_code = "fixture_failure"
                run.failure_message = "Falha controlada do teste"
                run.finished_at = datetime.now(UTC)
                session.commit()
            expect(
                page.get_by_text(
                    "Busca interrompida — resultado não confirmado no catálogo",
                    exact=True,
                )
            ).to_be_visible(timeout=15000)
            expect(page.get_by_text("Oferta E2E pichau", exact=True)).to_have_count(1)
            page.screenshot(path=str(artifacts / "failed.png"), full_page=True)
            with page.expect_response(
                lambda response: (
                    response.request.method == "POST" and "match-runs" in response.url
                )
            ) as started_response:
                page.get_by_role(
                    "button", name="Buscar preços em outras lojas", exact=True
                ).first.click()
            assert started_response.value.status == 202
            new_run_id = uuid.UUID(started_response.value.json()["id"])
            expect(
                page.get_by_role("button", name="Busca em andamento", exact=True).first
            ).to_be_disabled()
            expect(page.get_by_text("Oferta E2E pichau", exact=True)).to_have_count(0)
            with factory() as session:
                assert session.get(ProductMatchRun, new_run_id).status == "pending"
            assert not errors, errors
            assert not [entry for entry in requests if entry["status"] >= 400], requests
            report = {
                "commit_to_card_seconds": round(latency, 3),
                "requests": requests,
                "checks": [
                    "partial-before-terminal",
                    "review-separated",
                    "reload",
                    "terminal-handoff",
                    "draft-preserved",
                    "mobile",
                    "second-store-progressive",
                    "navigation-preserves-job",
                    "failed-preserves-observation",
                    "start-new-run-clears-previous-snapshot",
                ],
                "page_errors": errors,
            }
            (artifacts / "report.json").write_text(
                json.dumps(report, indent=2), encoding="utf-8"
            )
            print(
                json.dumps(
                    {key: value for key, value in report.items() if key != "requests"}
                )
            )
            browser.close()
    finally:
        with factory() as session:
            for run in session.query(ProductMatchRun).filter_by(product_id=product_id):
                session.delete(run)
            session.delete(session.get(CanonicalProduct, product_id))
            session.commit()
        engine.dispose()


if __name__ == "__main__":
    main()
