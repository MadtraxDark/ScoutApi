"""Read-only Chrome regression scenario using a populated local catalog.

Run with frontend localhost:3000 and API localhost:8000 available. Business
responses are real; only response timing and explicit transport errors are
controlled inside this dedicated browser context. No database writes.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from playwright.async_api import Error, async_playwright, expect


async def main() -> None:
    artifacts = Path(".tmp/catalog-changes-browser")
    artifacts.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel="chrome", headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 1000})
        page.set_default_timeout(10000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        started, release = asyncio.Event(), asyncio.Event()

        async def delay_response(route):
            response = await route.fetch()
            started.set()
            await release.wait()
            try:
                await route.fulfill(response=response)
            except Error:
                # Queries deliberately canceled by a newer query cannot fulfill.
                if not route.request.failure:
                    raise

        await page.route("**/products/changes*", delay_response)
        await page.goto("http://localhost:3000/admin/catalogo/alteracoes")
        await asyncio.wait_for(started.wait(), timeout=10)
        await expect(
            page.get_by_text("Carregando alterações...", exact=True)
        ).to_be_visible()
        assert not await page.get_by_role(
            "heading", name="Nenhuma alteração encontrada"
        ).count()
        release.set()
        region = page.get_by_role("region", name="Histórico registrado")
        await expect(region.locator("li").first).to_be_visible()
        await page.unroute("**/products/changes*", delay_response)
        search = page.get_by_label("Buscar no histórico", exact=True)
        title = await region.get_by_role("link").first.inner_text()
        query = title.split()[0]
        count = await region.locator("li").count()
        await page.evaluate("""() => {
          window.stableRegion = document.querySelector(
            '[aria-labelledby="changes-results-heading"]');
          window.stableList = window.stableRegion.querySelector('ul');
          window.stableForm = document.querySelector('form');
          window.layoutShifts = [];
          new PerformanceObserver(list => { for (const e of list.getEntries())
            window.layoutShifts.push({value:e.value,recent:e.hadRecentInput});
          }).observe({type:'layout-shift'});
        }""")

        async def geometry():
            return await page.evaluate("""() => {
              const region = document.querySelector(
                '[aria-labelledby="changes-results-heading"]');
              const input = document.querySelector('input[type="search"]');
              return { height:region.getBoundingClientRect().height,
                inputTop:input.getBoundingClientRect().top, scroll:scrollY,
                count:region.querySelectorAll('li').length,
                counter:region.querySelector('[aria-live]').textContent,
                sameRegion:window.stableRegion===region,
                sameList:window.stableList===region.querySelector('ul'),
                sameForm:window.stableForm===document.querySelector('form'),
                shifts:window.layoutShifts.slice() };
            }""")

        await search.click()
        await page.evaluate("window.scrollTo(0, 120)")
        before = await geometry()
        started.clear()
        release.clear()
        await page.route("**/products/changes*", delay_response)
        await search.fill(query)
        await asyncio.wait_for(started.wait(), timeout=5)
        await expect(
            page.get_by_text("Atualizando resultados...", exact=True)
        ).to_be_visible()
        during = await geometry()
        assert during["height"] == before["height"], (before, during)
        assert during["count"] == count and during["counter"] == before["counter"]
        assert (
            during["inputTop"] == before["inputTop"]
            and during["scroll"] == before["scroll"]
        )
        assert during["sameRegion"] and during["sameForm"] and during["sameList"]
        assert not await page.get_by_text(
            "Carregando alterações...", exact=True
        ).count()
        assert not await page.get_by_role(
            "heading", name="Nenhuma alteração encontrada"
        ).count()
        release.set()
        await expect(page.locator("mark").first).to_be_visible()
        await expect(
            page.get_by_text("Atualizando resultados...", exact=True)
        ).not_to_be_visible()
        assert (await geometry())["sameList"]
        await page.unroute("**/products/changes*", delay_response)
        print("stage=initial_refetch_geometry passed", flush=True)

        async def fail_response(route):
            await route.fulfill(
                status=503,
                content_type="application/json",
                body='{"detail":{"code":"DATABASE_UNAVAILABLE"}}',
                headers={
                    "access-control-allow-origin": "http://localhost:3000",
                    "access-control-allow-credentials": "true",
                },
            )

        await page.route("**/products/changes*", fail_response)
        retained = await geometry()
        marks = await page.locator("mark").all_text_contents()
        await search.fill("inexistente-refetch-estabilidade")
        await expect(
            page.get_by_text("Não foi possível atualizar os resultados.", exact=False)
        ).to_be_visible()
        failed = await geometry()
        assert (
            failed["count"] == retained["count"]
            and failed["counter"] == retained["counter"]
        )
        assert failed["height"] == retained["height"]
        assert await page.locator("mark").all_text_contents() == marks
        assert not await page.get_by_role(
            "heading", name="Nenhuma alteração encontrada"
        ).count()
        await page.unroute("**/products/changes*", fail_response)
        await page.get_by_role("button", name="Tentar novamente", exact=True).click()
        await expect(
            page.get_by_role("heading", name="Nenhuma alteração encontrada")
        ).to_be_visible()
        assert await region.locator("li").count() == 0
        print("stage=error_preserves_empty_after_success passed", flush=True)

        await page.get_by_role("button", name="Limpar filtros", exact=True).click()
        await expect(region.locator("li").first).to_be_visible()
        await expect(
            page.get_by_text("Atualizando resultados...", exact=True)
        ).not_to_be_visible()
        started.clear()
        release.clear()
        await page.route("**/products/changes*", delay_response)
        full = await geometry()
        previous_value = ""
        for value in ("580", "5800", "5800x", "5800x3", "5800x3d"):
            await search.press_sequentially(value[len(previous_value) :], delay=10)
            previous_value = value
            await page.wait_for_timeout(40)
            current = await geometry()
            assert (
                current["height"] == full["height"]
                and current["count"] == full["count"]
            )
        await asyncio.wait_for(started.wait(), timeout=5)
        assert (await geometry())["height"] == full["height"]
        release.set()
        await expect(
            page.get_by_text("Atualizando resultados...", exact=True)
        ).not_to_be_visible()
        await expect(search).to_be_focused()
        await page.unroute("**/products/changes*", delay_response)
        prefix_count = await region.locator("li").count()
        await search.fill("")
        await expect(region.locator("li").first).to_be_visible()
        await expect(
            page.get_by_text("Atualizando resultados...", exact=True)
        ).not_to_be_visible()
        await page.set_viewport_size({"width": 320, "height": 900})
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        mobile_before = await geometry()
        started.clear()
        release.clear()
        await page.route("**/products/changes*", delay_response)
        await search.fill(query)
        await asyncio.wait_for(started.wait(), timeout=5)
        mobile_during = await geometry()
        assert mobile_during["height"] == mobile_before["height"]
        assert mobile_during["inputTop"] == mobile_before["inputTop"]
        release.set()
        await expect(
            page.get_by_text("Atualizando resultados...", exact=True)
        ).not_to_be_visible()
        await page.unroute("**/products/changes*", delay_response)
        await page.route("**/products/changes*", fail_response)
        mobile_retained = await geometry()
        await search.fill("inexistente-refetch-mobile")
        await expect(
            page.get_by_text("Não foi possível atualizar os resultados.", exact=False)
        ).to_be_visible()
        assert (await geometry())["height"] == mobile_retained["height"]
        await page.unroute("**/products/changes*", fail_response)
        await page.screenshot(path=str(artifacts / "stable-mobile.png"), full_page=True)
        assert not errors, errors
        result = {
            "status": "passed",
            "before": before,
            "during": during,
            "prefix_final_count": prefix_count,
            "mobile_before": mobile_before,
            "mobile_during": mobile_during,
            "javascript_errors": errors,
            "checks": [
                "initial loading",
                "refetch retention",
                "stable geometry/scroll/count",
                "same container/list/form",
                "error retention/retry",
                "committed highlights",
                "empty after response",
                "580 through 5800x3d",
                "many-empty-many",
                "mobile",
            ],
        }
        (artifacts / "stability-result.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        print(json.dumps(result), flush=True)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
