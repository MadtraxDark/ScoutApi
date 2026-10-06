"""1/10/50 authenticated live observers, 4s cadence, isolated PostgreSQL only."""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import jwt
from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from tests.unit.test_match_run_progressive import _hit

from scout_api.modules.auth.schemas import AuthenticatedPrincipal, UserRole
from scout_api.modules.matching.match_run_service import MatchRunService
from scout_api.modules.matching.match_run_staging import stage_hit
from scout_api.modules.matching.models import CanonicalProduct, ProductMatchRun

TEST_JWT_SECRET = "progressive-fixture-only-not-a-real-secret"


async def main() -> None:
    address = os.environ["TEST_DATABASE_URL"]
    if make_url(address).database != "scout_progressive_test":
        raise RuntimeError("Exige banco isolado scout_progressive_test")
    seconds = int(os.environ.get("LOAD_SECONDS", "600"))
    engine = create_engine(address)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as session:
        product = CanonicalProduct(
            title="Fixture de carga progressiva", owner_user_id=None
        )
        session.add(product)
        session.flush()
        run = ProductMatchRun(
            product_id=product.id,
            status="running",
            worker_id="load-fixture",
            attempts=1,
            claim_expires_at=datetime.now(UTC) + timedelta(minutes=30),
        )
        session.add(run)
        session.flush()
        service = MatchRunService(session)
        stores = [
            "amazon_br",
            "amazon_us",
            "kabum",
            "terabyte",
            "pichau",
            "nissei",
            "comprasparaguai",
            "shopee",
            "visaovip",
        ]
        service.record_targets(run, stores=stores, reference_payload={})
        for store in stores[:4]:
            hit = _hit().model_copy(update={"store": store})
            currency = "USD" if store == "amazon_us" else "BRL"
            service.apply_store_outcome(
                run,
                store=store,
                display_name=store,
                status="match",
                duration_ms=10,
                queries=[],
                candidates_found=1,
                candidates_evaluated=1,
                matched_decision="auto_match",
                matched_payload=stage_hit(hit),
                matched_title=hit.product.title,
                matched_price=hit.product.price,
                matched_currency=currency,
            )
        session.commit()
        run_id, product_id = run.id, product.id
    query_count = 0

    def count_query(*args: object) -> None:
        nonlocal query_count
        query_count += 1

    event.listen(engine, "before_cursor_execute", count_query)
    with factory() as session:
        view = MatchRunService(session).get_live(
            run_id,
            principal=AuthenticatedPrincipal(id=uuid.uuid4(), role=UserRole.USER),
        )
        assert len(view.stores) == 9
    event.remove(engine, "before_cursor_execute", count_query)
    result = {
        n: {"latencies_ms": [], "payload_bytes": [], "statuses": {}}
        for n in (1, 10, 50)
    }
    client = httpx.AsyncClient(
        base_url="http://127.0.0.1:8011",
        timeout=20,
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=100),
    )
    started = time.monotonic()

    async def observer(group: int, index: int) -> None:
        token = jwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "aud": "authenticated",
                "exp": int(time.time()) + seconds + 600,
            },
            TEST_JWT_SECRET,
            algorithm="HS256",
        )
        # Spread steady-state reads over the polling period.
        deadline = started + (index / group) * 4
        while time.monotonic() - started < seconds:
            await asyncio.sleep(max(0, deadline - time.monotonic()))
            t0 = time.perf_counter()
            response = await client.get(
                f"/match-runs/{run_id}/live",
                headers={"Authorization": f"Bearer {token}"},
            )
            data = result[group]
            data["latencies_ms"].append((time.perf_counter() - t0) * 1000)
            data["payload_bytes"].append(len(response.content))
            data["statuses"][response.status_code] = (
                data["statuses"].get(response.status_code, 0) + 1
            )
            if response.status_code == 200:
                assert len(response.json()["stores"]) == 9
                assert (
                    "matched_payload" not in response.text
                    and "candidates" not in response.text
                )
            deadline = max(deadline + 4, time.monotonic())

    async def progress() -> None:
        while time.monotonic() - started < seconds:
            responses = sum(sum(d["statuses"].values()) for d in result.values())
            print(
                f"Carga live: {time.monotonic() - started:.0f}/{seconds}s; "
                f"respostas={responses}",
                flush=True,
            )
            await asyncio.sleep(min(30, max(0, seconds - (time.monotonic() - started))))

    try:
        await asyncio.gather(
            progress(),
            *(observer(group, index) for group in result for index in range(group)),
        )
        report = {
            "duration_seconds": round(time.monotonic() - started, 2),
            "interval_seconds": 4,
            "groups_run_concurrently": True,
            "data_queries_per_foreign_snapshot": query_count,
            "groups": {},
        }
        for group, data in result.items():
            latencies = sorted(data["latencies_ms"])
            report["groups"][group] = {
                "requests": len(latencies),
                "p50_ms": round(statistics.median(latencies), 2),
                "p95_ms": round(latencies[int((len(latencies) - 1) * 0.95)], 2),
                "max_ms": round(max(latencies), 2),
                "payload_max_bytes": max(data["payload_bytes"]),
                "statuses": data["statuses"],
            }
        path = Path(".tmp/progressive-browser/load-report.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report), flush=True)
        assert all(set(data["statuses"]) == {200} for data in result.values()), report
    finally:
        await client.aclose()
        with factory() as session:
            session.delete(session.get(ProductMatchRun, run_id))
            session.delete(session.get(CanonicalProduct, product_id))
            session.commit()
        engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
