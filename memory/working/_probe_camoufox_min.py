#!/usr/bin/env python3
"""Minimal Camoufox launch probe (3 locales) — no Product Match.

Run inside the api container after rebuild:
  docker compose exec api python /tmp/probe_camoufox_min.py
or copy via stdin.
"""

from __future__ import annotations

import time
from pathlib import Path

from camoufox.sync_api import Camoufox

from scout_api.core.config import get_settings
from scout_api.modules.crawler.services.html_fetcher import profile_dirs_for_base


def _probe(
    label: str, user_data_dir: Path, *, launch_timeout_ms: int
) -> dict[str, float | str]:
    user_data_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, float | str] = {"locale": label, "profile": str(user_data_dir)}
    t0 = time.perf_counter()
    try:
        with Camoufox(
            headless=True,
            humanize=False,
            persistent_context=True,
            user_data_dir=str(user_data_dir),
            timeout=launch_timeout_ms,
        ) as browser:
            out["browser_launch_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            page = browser.new_page()
            t1 = time.perf_counter()
            page.goto("about:blank", wait_until="domcontentloaded", timeout=15_000)
            out["navigation_ms"] = round((time.perf_counter() - t1) * 1000, 1)
            page.close()
        out["shutdown_ms"] = round(
            (time.perf_counter() - t0) * 1000 - float(out["browser_launch_ms"]),
            1,
        )
        out["result"] = "ok"
    except Exception as exc:  # noqa: BLE001
        out["browser_launch_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        out["result"] = f"error:{type(exc).__name__}:{exc}"
    return out


def main() -> None:
    settings = get_settings()
    base = Path(
        settings.camoufox_user_data_dir
        or "/home/app/.cache/scout-api/camoufox-profiles/default"
    )
    direct, _proxied = profile_dirs_for_base(base)
    launch_ms = int(settings.camoufox_launch_timeout_ms)
    locales = (
        ("locale_default", direct / "locale_default"),
        ("locale_pt_br", direct / "locale_pt_BR"),
        ("locale_en_us", direct / "locale_en_US"),
    )
    print(f"profile_root={base.parent} launch_timeout_ms={launch_ms}")
    for label, path in locales:
        row = _probe(label, path, launch_timeout_ms=launch_ms)
        print(row)


if __name__ == "__main__":
    main()
