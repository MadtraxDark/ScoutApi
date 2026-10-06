import json
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scout_api.core.config import get_settings
from scout_api.core.database import get_session_factory
from scout_api.modules.auth.deps import DEV_BYPASS_USER_ID
from scout_api.modules.matching.identity import identity_reference_item
from scout_api.modules.matching.match_run_service import MatchRunService
from scout_api.modules.matching.match_run_staging import stage_product
from scout_api.modules.matching.match_run_worker import process_claimed_run
from scout_api.modules.matching.models import CanonicalProduct, ProductMatchRun

factory = get_session_factory()
started = time.monotonic()
with factory() as session:
    product = CanonicalProduct(title="AMD Ryzen 7 9800X3D", brand="AMD", model="9800X3D",
                               owner_user_id=DEV_BYPASS_USER_ID)
    session.add(product)
    session.flush()
    run = ProductMatchRun(product_id=product.id, requested_by=DEV_BYPASS_USER_ID,
                         reference_url="https://www.kabum.com.br/produto/1",
                         status="running", worker_id="progressive-live-smoke", attempts=1,
                         claimed_at=datetime.now(UTC),
                         claim_expires_at=datetime.now(UTC) + timedelta(minutes=10))
    session.add(run)
    session.flush()
    MatchRunService(session).record_targets(run, ["kabum", "pichau"],
        stage_product(identity_reference_item(product.title, brand="AMD", model="9800X3D")))
    session.commit()
    run_id, product_id = run.id, product.id

stop = threading.Event()
def observe():
    while not stop.wait(10):
        with factory() as session:
            row = session.get(ProductMatchRun, run_id)
            print(f"Smoke real: {time.monotonic()-started:.0f}s; "
                  f"run={row.status}; lojas={[(s.store,s.status) for s in row.store_runs]}", flush=True)

threading.Thread(target=observe, daemon=True).start()
try:
    with factory() as session:
        result = process_claimed_run(session, session.get(ProductMatchRun, run_id),
                                    worker_id="progressive-live-smoke", settings=get_settings())
        session.commit()
        report = {"duration_seconds": round(time.monotonic()-started, 2), "status": result.status,
                  "failure_code": result.failure_code,
                  "stores": [{"store": s.store, "status": s.status, "decision": s.matched_decision,
                              "error_code": s.error_code, "duration_ms": s.duration_ms,
                              "listing_linked": s.matched_listing_id is not None} for s in result.store_runs]}
        Path('.tmp/progressive-browser/smoke-report.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report), flush=True)
finally:
    stop.set()
    with factory() as session:
        row = session.get(ProductMatchRun, run_id)
        for notification in MatchRunService(session)._notifications.list_for_user(DEV_BYPASS_USER_ID):
            if notification.metadata_json.get('run_id') == str(run_id):
                session.delete(notification)
        session.delete(row)
        session.delete(session.get(CanonicalProduct, product_id))
        session.commit()
