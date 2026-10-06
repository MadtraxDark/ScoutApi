"""Live Amazon BR multi-category search + optional MatchRun smoke."""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from scout_api.modules.crawler.services.product_scrape_service import (
    get_shared_html_fetcher,
)
from scout_api.modules.matching.store_search_service import StoreSearchService

QUERIES = [
    ("smartphone", "samsung galaxy s25 ultra 256gb"),
    ("cpu", "processador ryzen 7 5700x"),
    ("ssd", "ssd samsung 990 evo plus 1tb"),
    ("eletrônico", "iphone 16 128gb"),
]


def main() -> None:
    search = StoreSearchService(fetcher=get_shared_html_fetcher())
    report: dict = {
        "started_at": datetime.now(UTC).isoformat(),
        "searches": [],
    }
    for category, query in QUERIES:
        print(f"=== {category}: {query!r} ===", flush=True)
        t0 = time.perf_counter()
        row: dict = {"category": category, "query": query}
        try:
            cands = search.search("amazon_br", query, limit=8)
            ms = (time.perf_counter() - t0) * 1000
            row.update(
                {
                    "ok": True,
                    "ms": round(ms, 1),
                    "raw_count": len(cands),
                    "asins": [c.product_id for c in cands],
                    "titles": [(c.title or "")[:100] for c in cands],
                    "error": None,
                }
            )
            print(
                f"  ok ms={row['ms']} count={row['raw_count']} "
                f"asins={row['asins'][:5]}",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001
            ms = (time.perf_counter() - t0) * 1000
            row.update(
                {
                    "ok": False,
                    "ms": round(ms, 1),
                    "raw_count": 0,
                    "asins": [],
                    "titles": [],
                    "error": f"{type(exc).__name__}: {exc}",
                    "error_code": getattr(exc, "code", None),
                }
            )
            print(f"  FAIL ms={row['ms']} {row['error']}", flush=True)
        report["searches"].append(row)

    report["finished_at"] = datetime.now(UTC).isoformat()
    out = (
        Path(__file__).resolve().parents[1]
        / "data"
        / "live-match-reports"
        / f"amazon_br_live_cats_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {out}", flush=True)
    failed = [s for s in report["searches"] if not s["ok"]]
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
