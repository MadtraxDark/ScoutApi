"""One-off live fetch for Pichau Ryzen baseline evidence (no spider changes)."""

from __future__ import annotations

from pathlib import Path

from scout_api.modules.crawler.services.product_scrape_service import (
    get_shared_html_fetcher,
)

URL = (
    "https://www.pichau.com.br/processador-amd-ryzen-7-5800x3d-8-core-16-threads"
    "-3-4ghz-4-5ghz-turbo-cache-100mb-am4-100-100000651pof"
)
OUT = Path("data/_pichau_ryzen_live.html")
META = Path("memory/working/_pichau_camoufox_meta.txt")


def main() -> None:
    fetcher = get_shared_html_fetcher()
    print("fetcher", type(fetcher).__name__, flush=True)
    resp = fetcher.fetch(URL)
    html = resp.html or ""
    OUT.write_text(html, encoding="utf-8")
    lines = [
        f"status={getattr(resp, 'status_code', None)}",
        f"strategy={getattr(resp, 'fetch_strategy', None)}",
        f"final_url={getattr(resp, 'final_url', None)}",
        f"content_type={getattr(resp, 'content_type', None)}",
        f"html_chars={len(html)}",
        f"html_bytes={len(html.encode('utf-8'))}",
        f"saved={OUT}",
        f"title_snip={html[:180].replace(chr(10), ' ')}",
    ]
    META.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines), flush=True)


if __name__ == "__main__":
    main()
